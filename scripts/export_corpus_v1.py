"""Export the loaded books table to data/corpus_v1.json.

Lean schema: only fields the frontend will display. Source of truth is
the DB (post-load), not corpus_merged_v1.csv — that way the JSON
matches what the API serves.

Usage:
    python scripts/export_corpus_v1.py
"""
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book  # noqa: E402


OUTPUT_PATH = REPO_ROOT / "data" / "corpus_v1.json"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    engine = create_engine(db_url)
    with Session(engine) as session:
        books = session.execute(
            select(Book).order_by(Book.title)
        ).scalars().all()

        records = [
            {
                "id": str(b.id),
                "title": b.title,
                "author": b.author,
                "isbn_13": b.isbn_13,
                "publication_year": b.publication_year,
                "page_count": b.page_count,
                "cover_url": b.cover_url,
                "source": b.source,
                "description": b.description,
            }
            for b in books
        ]

    # Atomic write via .tmp + replace to avoid half-written files.
    tmp_path = OUTPUT_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, OUTPUT_PATH)

    print(f"OK: wrote {len(records)} books to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
