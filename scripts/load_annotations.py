"""Load auto-annotations from CSV into book_concept_annotations.

Phase 6.4 second half. Reads data/auto_annotations_v1.csv (produced by
auto_annotate.py) and inserts rows with annotation_type='auto'.

Safety guarantees (agreed in Phase 6.1 design):
  - Manual annotations are authoritative and NEVER touched. Any CSV book
    that somehow has manual annotation rows is skipped entirely (the
    batch source query excluded annotated books, so overlap means
    something drifted — it is reported loudly).
  - Idempotent: all existing annotation_type='auto' rows are deleted
    before insert, so re-running converges to the CSV contents.
  - --dry-run previews everything without committing.

Usage:
    python scripts/load_annotations.py --dry-run    # preview
    python scripts/load_annotations.py              # load

Requires DATABASE_URL in env.
"""
import argparse
import csv
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402

INPUT_CSV = REPO_ROOT / "data" / "auto_annotations_v1.csv"


def count_annotated_books(session: Session) -> int:
    return session.scalar(
        select(func.count(func.distinct(BookConceptAnnotation.book_id)))
    ) or 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Load auto-annotations CSV into the DB.")
    parser.add_argument("--input", type=Path, default=INPUT_CSV)
    parser.add_argument("--dry-run", action="store_true", help="preview without committing")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2
    if not args.input.exists():
        print(f"ERROR: {args.input} not found — run auto_annotate.py first")
        return 2

    # ---- Read + group the CSV ----
    rows_by_book: dict[str, list[dict]] = defaultdict(list)
    with open(args.input, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows_by_book[row["book_id"]].append(row)
    print(f"CSV: {sum(len(v) for v in rows_by_book.values())} rows "
          f"across {len(rows_by_book)} books")

    engine = create_engine(db_url)
    with Session(engine) as session:
        # ---- Resolve slugs -> concept ids ----
        slug_to_id = {
            slug: cid for cid, slug in session.execute(
                select(Concept.id, Concept.slug)
            ).all()
        }

        # ---- Guard: never touch books that have manual annotations ----
        manual_book_ids = {
            str(bid) for bid in session.execute(
                select(BookConceptAnnotation.book_id)
                .where(BookConceptAnnotation.annotation_type == "manual")
                .distinct()
            ).scalars().all()
        }
        overlap = set(rows_by_book) & manual_book_ids
        if overlap:
            print(f"WARNING: {len(overlap)} CSV books already have MANUAL "
                  f"annotations — skipping them (manual is authoritative):")
            for bid in list(overlap)[:5]:
                print(f"  - {rows_by_book[bid][0]['title']}")
            for bid in overlap:
                del rows_by_book[bid]

        # ---- Validate slugs before touching anything ----
        bad_slugs = {
            r["concept_slug"]
            for rows in rows_by_book.values() for r in rows
            if r["concept_slug"] not in slug_to_id
        }
        if bad_slugs:
            print(f"ERROR: {len(bad_slugs)} unknown concept slugs in CSV: "
                  f"{sorted(bad_slugs)[:10]}")
            return 2

        before = count_annotated_books(session)

        # ---- Idempotent reload of all auto rows ----
        deleted = session.execute(
            delete(BookConceptAnnotation)
            .where(BookConceptAnnotation.annotation_type == "auto")
        ).rowcount
        inserted = 0
        for bid, rows in rows_by_book.items():
            # Defensive dedupe: keep highest strength per (book, concept).
            best: dict[str, dict] = {}
            for r in rows:
                k = r["concept_slug"]
                if k not in best or float(r["strength"]) > float(best[k]["strength"]):
                    best[k] = r
            for r in best.values():
                session.add(BookConceptAnnotation(
                    book_id=uuid.UUID(bid),
                    concept_id=slug_to_id[r["concept_slug"]],
                    annotation_type="auto",
                    strength=float(r["strength"]),
                ))
                inserted += 1

        session.flush()
        after = count_annotated_books(session)

        print()
        print(f"Existing auto rows deleted: {deleted}")
        print(f"Auto rows inserted:         {inserted}")
        print(f"Annotated books:            {before} -> {after} (of "
              f"{session.scalar(select(func.count(Book.id)))})")

        if args.dry_run:
            session.rollback()
            print("\n--dry-run: rolled back, nothing committed.")
        else:
            session.commit()
            print("\nCommitted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
