"""Track D: serve the investor / trader learning roadmaps with per-user progress.

The roadmaps are a static, curated artifact (data/roadmaps_v1.json) produced by
scripts/build_roadmaps.py. Each rung names a book by title; we resolve it to the
current DB's Book at request time (by normalized title) so the artifact stays
portable across the local and production databases, which assign different
book_ids. Per-user progress (read / next) is overlaid from the caller's library.
"""
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book

# backend/app/services/roadmaps.py -> repo root is parents[3]
ROADMAPS_PATH = Path(__file__).resolve().parents[3] / "data" / "roadmaps_v1.json"
VALID_ROLES = ("investor", "trader")


def _norm(t: str) -> str:
    t = (t or "").lower().split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(ROADMAPS_PATH.read_text(encoding="utf-8"))


def build_roadmap(
    session: Session,
    role: str,
    read_book_ids: set[uuid.UUID],
) -> dict:
    """Resolve a role's roadmap rungs to catalog books and overlay read/next."""
    rungs_raw = _load().get(role, [])

    curated = session.execute(select(Book).where(Book.curated.is_(True))).scalars().all()
    by_title: dict[str, Book] = {}
    for b in curated:
        by_title.setdefault(_norm(b.title), b)

    rungs = []
    read_count = 0
    for r in rungs_raw:
        book = by_title.get(_norm(r.get("title", "")))
        is_read = bool(book and book.id in read_book_ids)
        if is_read:
            read_count += 1
        rungs.append({
            "rung": r.get("rung"),
            "book_id": book.id if book else None,
            "title": r.get("title", ""),
            "author": book.author if book else r.get("author", ""),
            "cover_url": book.cover_url if book else None,
            "new_concepts": r.get("new_concepts", []),
            "why": r.get("why", ""),
            "read": is_read,
            "is_next": False,
        })

    # The "next" rung is the first unread one that actually resolves to a book.
    for rung in rungs:
        if not rung["read"] and rung["book_id"] is not None:
            rung["is_next"] = True
            break

    return {"role": role, "rungs": rungs, "read_count": read_count, "total": len(rungs)}
