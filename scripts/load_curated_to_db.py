"""Convergence step 2: load the curated catalog into the DB (LOCAL first).

Idempotent. Each run:
  1. Inserts the 9 must-adds as Book rows (or matches an existing one by ISBN).
  2. Resets curated=false on ALL books, then sets curated=true for the 232 catalog
     books (matched by ISBN, else normalized title+author surname).
  3. Replaces the must-adds' manual_grounded annotations from grounded_annotations_v1.csv.
  4. Sets difficulty_tier on the must-adds from book_difficulty_v1.csv.

Books are NOT deleted -- the dropped scrape stays in the DB but curated=false hides
it from recommendations/search.

Inputs (read-only):
  data/curated_core_catalog_v2.csv, data/must_adds_v1.csv,
  data/grounded_annotations_v1.csv, data/book_difficulty_v1.csv
Requires DATABASE_URL (defaults to local Docker) in env/.env.

Usage:
    python scripts/load_curated_to_db.py
"""
from __future__ import annotations

import csv
import os
import re
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402

CATALOG = Path("data/curated_core_catalog_v2.csv")
MUSTADDS = Path("data/must_adds_v1.csv")
GROUNDED = Path("data/grounded_annotations_v1.csv")
DIFFICULTY = Path("data/book_difficulty_v1.csv")
DEFAULT_DB = "postgresql://atlas:atlas@localhost:5432/atlas_dev"


def norm(t: str) -> str:
    t = (t or "").lower().split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def surname(a: str) -> str:
    first = (a or "").split(",")[0].strip()
    parts = first.split()
    return parts[-1].lower() if parts else ""


def _read(p: Path) -> list[dict]:
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL") or DEFAULT_DB
    must_gvids = {(r.get("google_volume_id") or "").strip() for r in _read(MUSTADDS)}

    engine = create_engine(db_url)
    with Session(engine) as s:
        # --- DB book indexes ---
        rows = s.execute(select(Book.id, Book.title, Book.author, Book.isbn_13)).all()
        by_isbn = {}
        by_title = {}
        for bid, title, author, isbn in rows:
            if isbn:
                by_isbn[isbn.strip()] = bid
            by_title.setdefault((norm(title), surname(author)), bid)
        slug2id = {c.slug: c.id for c in s.execute(select(Concept)).scalars().all()}

        catalog = _read(CATALOG)
        matched: set = set()
        mustadd_bid: dict[str, uuid.UUID] = {}   # google_volume_id -> book_id
        unmatched: list[str] = []
        inserted = 0

        for r in catalog:
            isbn = (r.get("isbn_13") or "").strip()
            cisbn = (r.get("canonical_isbn_13") or "").strip()
            nt, sn = norm(r.get("title", "")), surname(r.get("author", ""))
            gvid = (r.get("google_volume_id") or "").strip()
            bid = by_isbn.get(isbn) or by_isbn.get(cisbn) or by_title.get((nt, sn))

            if r.get("keep_source") == "manual_add":
                if bid is None:
                    book = Book(
                        title=r.get("title", ""),
                        author=r.get("author", ""),
                        isbn_13=(cisbn or isbn or None) or None,
                        description=r.get("description") or None,
                        publication_year=_int_or_none(r.get("publication_year")),
                        cover_url=r.get("cover_url") or None,
                        source="manual_add",
                        curated=True,
                    )
                    s.add(book)
                    s.flush()
                    bid = book.id
                    inserted += 1
                mustadd_bid[gvid] = bid
                matched.add(bid)
            elif bid is not None:
                matched.add(bid)
            else:
                unmatched.append(r.get("title", ""))

        # --- flag curated: reset all, then set the matched 232 ---
        s.execute(update(Book).values(curated=False))
        if matched:
            s.execute(update(Book).where(Book.id.in_(matched)).values(curated=True))

        # --- grounded annotations for must-adds (replace) ---
        bids = list(mustadd_bid.values())
        if bids:
            s.execute(
                delete(BookConceptAnnotation).where(
                    BookConceptAnnotation.book_id.in_(bids),
                    BookConceptAnnotation.annotation_type == "manual_grounded",
                )
            )
        ann_ins = 0
        missing_slugs: set = set()
        for r in _read(GROUNDED):
            bid = mustadd_bid.get((r.get("google_volume_id") or "").strip())
            if bid is None:
                continue
            cid = slug2id.get(r.get("concept_slug", ""))
            if cid is None:
                missing_slugs.add(r.get("concept_slug", ""))
                continue
            try:
                strength = float(r.get("strength"))
            except (TypeError, ValueError):
                continue
            s.add(BookConceptAnnotation(
                book_id=bid, concept_id=cid,
                annotation_type="manual_grounded", strength=strength,
            ))
            ann_ins += 1

        # --- difficulty tiers ---
        diff_set = 0
        for r in _read(DIFFICULTY):
            bid = mustadd_bid.get((r.get("google_volume_id") or "").strip())
            dt = _int_or_none(r.get("difficulty_tier"))
            if bid is not None and dt is not None:
                s.execute(update(Book).where(Book.id == bid).values(difficulty_tier=dt))
                diff_set += 1

        s.commit()
        n_curated = s.execute(
            select(Book).where(Book.curated.is_(True))
        ).scalars().all()

    print(f"DB: {db_url.split('@')[-1]}")
    print(f"  must-adds inserted (new Book rows): {inserted}/{len(must_gvids)}")
    print(f"  curated=true books now: {len(n_curated)}  (target 232)")
    print(f"  grounded annotations loaded: {ann_ins}")
    print(f"  difficulty tiers set: {diff_set}")
    if unmatched:
        print(f"\n  UNMATCHED catalog books ({len(unmatched)}) -- not flagged curated:")
        for t in unmatched:
            print(f"    - {t}")
    if missing_slugs:
        print(f"\n  WARNING: concept slugs not in DB: {sorted(missing_slugs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
