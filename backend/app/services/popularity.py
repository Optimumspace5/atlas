"""Popularity baseline ranker.

Ranks books by raw annotation count — a book that has been annotated
against many concepts is treated as 'more popular' (read: broadly tagged).

This is the dumbest possible recommendation strategy and exists purely
to give smarter strategies (gap, tfidf, etc.) a floor to beat.

The function is symmetric with rank_candidates() in gap_scoring.py:
    - takes a session, a list of read book IDs to exclude, and a top_k
    - returns list[tuple[Book, float]] sorted by score descending
The shape match means the router can dispatch to either strategy
without callers needing to know the difference.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import Book, BookConceptAnnotation


def rank_by_popularity(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k: int,
) -> list[tuple[Book, float]]:
    """Return the top_k most-annotated books, excluding already-read.

    'Score' here is the raw annotation count cast to float (so the response
    schema's `score: float` field stays consistent across strategies).
    Books with zero annotations are excluded — they have nothing to rank by.
    """
    read_set = set(read_book_ids)

    stmt = (
        select(Book, func.count(BookConceptAnnotation.book_id).label("score"))
        .join(BookConceptAnnotation, BookConceptAnnotation.book_id == Book.id)
        .group_by(Book.id)
        .order_by(func.count(BookConceptAnnotation.book_id).desc())
    )

    rows = session.execute(stmt).all()

    # Filter out already-read books AFTER the GROUP BY so we don't lose
    # the popularity ranking semantics. Then take top_k.
    filtered = [
        (book, float(score))
        for book, score in rows
        if book.id not in read_set
    ]
    return filtered[:top_k]
