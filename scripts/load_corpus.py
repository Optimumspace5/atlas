"""Load books from data/corpus_merged_v1.csv into the books table.

Reads each row of the merged corpus CSV and inserts a corresponding row
into the books table via the SQLAlchemy ORM. Idempotent: re-running the
script does not create duplicates.

Idempotency strategy:
    - If the row has a canonical_isbn_13, check for an existing book by
      isbn_13. If found, skip; otherwise insert.
    - If the row has no canonical_isbn_13, check for an existing book by
      (title, author). If found, skip; otherwise insert with isbn_13=NULL.

Conflict action is always DO NOTHING. This loader never overwrites an
existing book's fields. A separate refresh_corpus.py script will exist
later if/when refresh semantics are needed.

Usage:
    python scripts/load_corpus.py                          # uses default CSV path
    python scripts/load_corpus.py --csv path/to/other.csv
    python scripts/load_corpus.py --dry-run                # prints actions, no DB writes

Requires DATABASE_URL in env, e.g.:
    $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book # noqa:E402 (import after sys.path tweak)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DEFAULT_CSV = REPO_ROOT / "data" / "corpus_merged_v1.csv"

# Log prefixes — fixed-width so the output aligns visually.
PREFIX_ADD = "[ADD ]"
PREFIX_SKIP_ISBN = "[SKIP isbn]"
PREFIX_SKIP_DUP = "[SKIP dup ]"
PREFIX_FAIL = "[FAIL]"

# -----------------------------------------------------------------------------
# Value-conversion helpers
# -----------------------------------------------------------------------------
def empty_to_none(value: str | None) -> str | None:
    """Convert empty strings to None for nullable DB columns.

    CSV readers return "" for missing cells; the DB wants NULL. Whitespace-only
    strings are also treated as empty.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def parse_optional_int(value: str | None) -> int | None:
    """Convert a CSV string to int, or None if empty/invalid.

    Used for page_count and publication_year, which are nullable integers
    in the books table. Returns None on any parse failure rather than
    raising, since corpus CSV values come from third-party APIs and may
    occasionally be malformed (e.g. publication_year="n.d.").
    """
    cleaned = empty_to_none(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None

# -----------------------------------------------------------------------------
# Idempotency lookup
# -----------------------------------------------------------------------------
def book_exists(
    session: Session,
    isbn_13: str | None,
    title: str,
    author: str,
) -> bool:
    """Return True if a book matching the row already exists in the DB.

    Lookup strategy:
        - If isbn_13 is provided, match on books.isbn_13 (UNIQUE column).
        - If isbn_13 is None, match on (title, author) as a fallback.

    The two strategies are intentionally disjoint: a row with an ISBN is
    NEVER checked against title+author, even if its (title, author) collides
    with an existing NULL-ISBN book. This is by design — the ISBN is the
    authoritative identity, and two editions of the same title can legitimately
    share (title, author) while having different ISBNs.
    """
    if isbn_13 is not None:
        stmt = select(Book.id).where(Book.isbn_13 == isbn_13)
    else:
        stmt = select(Book.id).where(
            Book.title == title,
            Book.author == author,
            Book.isbn_13.is_(None),
        )
    return session.scalar(stmt) is not None


# -----------------------------------------------------------------------------
# Per-row processing
# -----------------------------------------------------------------------------
def load_row(
    session: Session,
    row: dict[str, str],
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Process one CSV row: skip if a matching book already exists, else add.

    Updates `stats` in place and prints a single log line describing the
    action taken. The session is NOT committed here — the caller commits
    once at the end of the run.
    """
    stats["total"] += 1

    title = empty_to_none(row.get("title"))
    author = empty_to_none(row.get("author"))

    # Title and author are NOT NULL in the books table. A row missing either
    # is malformed; log and skip rather than letting the INSERT crash later.
    if not title or not author:
        stats["failed"] += 1
        print(f"{PREFIX_FAIL} missing title or author "
              f"(title={title!r}, author={author!r})")
        return

    isbn_13 = empty_to_none(row.get("canonical_isbn_13"))

    if book_exists(session, isbn_13, title, author):
        if isbn_13 is not None:
            stats["skipped_isbn"] += 1
            print(f"{PREFIX_SKIP_ISBN} {isbn_13}  {title} — {author}")
        else:
            stats["skipped_dup"] += 1
            print(f"{PREFIX_SKIP_DUP}                {title} — {author}")
        return

    book = Book(
        title=title,
        author=author,
        isbn_13=isbn_13,
        description=empty_to_none(row.get("description")),
        page_count=parse_optional_int(row.get("page_count")),
        publication_year=parse_optional_int(row.get("publication_year")),
        cover_url=empty_to_none(row.get("cover_url")),
        source=empty_to_none(row.get("source")) or "manual",
    )

    if not dry_run:
        session.add(book)

    stats["added"] += 1
    isbn_label = isbn_13 if isbn_13 else "(no-isbn)   "
    print(f"{PREFIX_ADD} {isbn_label}  {title} — {author}")


# -----------------------------------------------------------------------------
# Main loader
# -----------------------------------------------------------------------------
def load_corpus(csv_path: Path, dry_run: bool) -> int:
    """Drive the load: open session, iterate CSV, commit, print summary.

    Returns a process exit code:
        0 = clean success
        1 = ran, but some rows failed (or invariant violated)
        2 = setup failure (missing env var or CSV file)
    """
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
    print(f"--- load_corpus.py ({mode_label}) ---")
    print(f"CSV:    {csv_path}")
    # Hide credentials when logging the DB target.
    print(f"DB:     {db_url.rsplit('@', 1)[-1]}")
    print()

    engine = create_engine(db_url)
    stats = {
        "total": 0,
        "added": 0,
        "skipped_isbn": 0,
        "skipped_dup": 0,
        "failed": 0,
    }

    with Session(engine) as session:
        try:
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    load_row(session, row, stats, dry_run)

            if dry_run:
                session.rollback()
            else:
                session.commit()
        except Exception:
            session.rollback()
            raise

    print()
    print("--- Summary ---")
    print(f"  Total rows processed:      {stats['total']}")
    print(f"  Added:                     {stats['added']}")
    print(f"  Skipped (existing isbn):   {stats['skipped_isbn']}")
    print(f"  Skipped (existing title):  {stats['skipped_dup']}")
    print(f"  Failed (malformed row):    {stats['failed']}")

    accounted = (
        stats["added"]
        + stats["skipped_isbn"]
        + stats["skipped_dup"]
        + stats["failed"]
    )
    if accounted != stats["total"]:
        print(
            f"  WARNING: counts do not sum to total "
            f"({accounted} vs {stats['total']})"
        )
        return 1

    if dry_run:
        print()
        print("DRY-RUN: no changes committed.")

    return 0 if stats["failed"] == 0 else 1


# -----------------------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load books from a corpus CSV into the books table.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to corpus CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    args = parser.parse_args()
    return load_corpus(csv_path=args.csv, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

