"""Load annotations from data/annotations_v1.csv into book_concept_annotations.

For each row, resolve book_id (by canonical_isbn_13, falling back to
(title, author) for NULL-ISBN rows) and concept_id (by concept_slug),
then insert into the composite-PK annotations table.

Idempotency: pre-check by composite PK (book_id, concept_id, annotation_type)
before inserting. Re-runs are safe and produce zero adds.

Failure handling:
    - Missing book or missing concept: log a FAIL line, increment counter,
      keep processing. Exit code 1 if any failures occurred.
    - Unknown annotation_type or invalid strength: same — log + count.
    - The loader never aborts mid-run on row-level failures; you see all
      problems in one pass.

Strength values must be one of {1.0, 0.5, 0.3} (mirrors DB CHECK constraint).
annotation_type values must be one of {'manual', 'manual_audit', 'model'}
(enforced by this loader; the DB column has no CHECK constraint).

Usage:
    python scripts/load_annotations.py
    python scripts/load_annotations.py --csv path/to/other.csv
    python scripts/load_annotations.py --dry-run

Requires DATABASE_URL in env, e.g.:
    $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"
"""
import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, Concept, BookConceptAnnotation  # noqa: E402


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DEFAULT_CSV = REPO_ROOT / "data" / "annotations_v1.csv"

PREFIX_ADD = "[ADD ]"
PREFIX_SKIP = "[SKIP]"
PREFIX_FAIL = "[FAIL]"

VALID_ANNOTATION_TYPES = {"manual", "manual_audit", "model"}
VALID_STRENGTHS = {1.0, 0.5, 0.3}


# -----------------------------------------------------------------------------
# Value-conversion helpers
# -----------------------------------------------------------------------------
def empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def parse_strength(value: str | None) -> float | None:
    """Parse strength as float; return None if missing or not in allowed set."""
    cleaned = empty_to_none(value)
    if cleaned is None:
        return None
    try:
        f = float(cleaned)
    except ValueError:
        return None
    return f if f in VALID_STRENGTHS else None


def parse_created_at(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None if missing or unparseable."""
    cleaned = empty_to_none(value)
    if cleaned is None:
        return None
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# FK lookups
# -----------------------------------------------------------------------------
def lookup_book(session: Session, row: dict[str, str]) -> Book | None:
    """Find the Book matching this annotation row.

    Strategy mirrors load_corpus.py's idempotency check:
        - If canonical_isbn_13 is present, match on books.isbn_13.
        - Otherwise, match on (title, author) restricted to NULL-ISBN books.
    """
    isbn = empty_to_none(row.get("canonical_isbn_13"))
    if isbn:
        return session.scalar(select(Book).where(Book.isbn_13 == isbn))

    title = empty_to_none(row.get("title"))
    author = empty_to_none(row.get("author"))
    if not title or not author:
        return None
    return session.scalar(
        select(Book).where(
            Book.title == title,
            Book.author == author,
            Book.isbn_13.is_(None),
        )
    )


def lookup_concept(session: Session, slug: str) -> Concept | None:
    return session.scalar(select(Concept).where(Concept.slug == slug))


def annotation_exists(
    session: Session,
    book_id,
    concept_id,
    annotation_type: str,
) -> bool:
    """Composite-PK existence check."""
    stmt = select(BookConceptAnnotation.book_id).where(
        BookConceptAnnotation.book_id == book_id,
        BookConceptAnnotation.concept_id == concept_id,
        BookConceptAnnotation.annotation_type == annotation_type,
    )
    return session.scalar(stmt) is not None


# -----------------------------------------------------------------------------
# Per-row processing
# -----------------------------------------------------------------------------
def process_row(
    session: Session,
    row: dict[str, str],
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Process one annotation row. Updates stats in place and prints a log line."""
    stats["total"] += 1

    # Identifying label used in log lines.
    title = row.get("title", "?")
    slug = empty_to_none(row.get("concept_slug")) or "?"
    label = f"{title!r} ~ {slug}"

    # Validate annotation_type.
    annotation_type = empty_to_none(row.get("annotation_type"))
    if annotation_type not in VALID_ANNOTATION_TYPES:
        stats["failed_bad_type"] += 1
        print(f"{PREFIX_FAIL} {label}  bad annotation_type={annotation_type!r}")
        return

    # Validate strength.
    strength = parse_strength(row.get("strength"))
    if strength is None:
        stats["failed_bad_strength"] += 1
        print(f"{PREFIX_FAIL} {label}  bad strength={row.get('strength')!r}")
        return

    # Resolve concept_slug.
    concept_slug = empty_to_none(row.get("concept_slug"))
    if concept_slug is None:
        stats["failed_no_concept"] += 1
        print(f"{PREFIX_FAIL} {label}  missing concept_slug")
        return
    concept = lookup_concept(session, concept_slug)
    if concept is None:
        stats["failed_no_concept"] += 1
        print(f"{PREFIX_FAIL} {label}  concept not found: {concept_slug!r}")
        return

    # Resolve book.
    book = lookup_book(session, row)
    if book is None:
        stats["failed_no_book"] += 1
        isbn = empty_to_none(row.get("canonical_isbn_13"))
        key = f"isbn={isbn}" if isbn else f"title+author={title!r}/{row.get('author')!r}"
        print(f"{PREFIX_FAIL} {label}  book not found ({key})")
        return

    # Composite PK existence check.
    if annotation_exists(session, book.id, concept.id, annotation_type):
        stats["skipped_exists"] += 1
        print(f"{PREFIX_SKIP} {label}  (already present)")
        return

    # Insert.
    created_at = parse_created_at(row.get("created_at"))
    annotation = BookConceptAnnotation(
        book_id=book.id,
        concept_id=concept.id,
        annotation_type=annotation_type,
        strength=strength,
    )
    if created_at is not None:
        annotation.created_at = created_at  # override server_default

    if not dry_run:
        session.add(annotation)

    stats["added"] += 1
    print(f"{PREFIX_ADD} {label}  strength={strength} type={annotation_type}")


# -----------------------------------------------------------------------------
# Main loader
# -----------------------------------------------------------------------------
def load_annotations(csv_path: Path, dry_run: bool) -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set. Example:")
        print('  $env:DATABASE_URL = '
              '"postgresql://atlas:atlas@localhost:5432/atlas_dev"')
        return 2

    if not csv_path.exists():
        print(f"ERROR: CSV not found at {csv_path}")
        return 2

    mode_label = "DRY-RUN" if dry_run else "LIVE"
    print(f"--- load_annotations.py ({mode_label}) ---")
    print(f"CSV:    {csv_path}")
    print(f"DB:     {db_url.rsplit('@', 1)[-1]}")
    print()

    engine = create_engine(db_url)
    stats = {
        "total": 0,
        "added": 0,
        "skipped_exists": 0,
        "failed_no_book": 0,
        "failed_no_concept": 0,
        "failed_bad_type": 0,
        "failed_bad_strength": 0,
    }

    with Session(engine) as session:
        try:
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    process_row(session, row, stats, dry_run)

            if dry_run:
                session.rollback()
            else:
                session.commit()
        except Exception:
            session.rollback()
            raise

    print()
    print("--- Summary ---")
    print(f"  Total rows processed:        {stats['total']}")
    print(f"  Added:                       {stats['added']}")
    print(f"  Skipped (already present):   {stats['skipped_exists']}")
    print(f"  Failed (book not found):     {stats['failed_no_book']}")
    print(f"  Failed (concept not found):  {stats['failed_no_concept']}")
    print(f"  Failed (bad annotation_type):{stats['failed_bad_type']}")
    print(f"  Failed (bad strength):       {stats['failed_bad_strength']}")

    total_failed = (
        stats["failed_no_book"]
        + stats["failed_no_concept"]
        + stats["failed_bad_type"]
        + stats["failed_bad_strength"]
    )
    accounted = stats["added"] + stats["skipped_exists"] + total_failed
    if accounted != stats["total"]:
        print(
            f"  WARNING: counts do not sum to total "
            f"({accounted} vs {stats['total']})"
        )
        return 1

    if dry_run:
        print()
        print("DRY-RUN: no changes committed.")

    return 0 if total_failed == 0 else 1


# -----------------------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load annotations CSV into book_concept_annotations.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to annotations CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    args = parser.parse_args()
    return load_annotations(csv_path=args.csv, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
