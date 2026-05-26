"""POST /recommendations — gap-fill book recommendations.

Wraps the rank_candidates() pipeline behind an HTTP endpoint. The candidate
pool is every book in the catalog; rank_candidates filters out already-read
books and sorts the rest by gap-fill score. We slice to top_k.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Book
from backend.app.schemas import (
    BookResult,
    RecommendationRequest,
    RecommendationResponse,
)
from backend.app.services.gap_scoring import rank_candidates


router = APIRouter()


@router.post("/recommendations", response_model=RecommendationResponse)
def recommend(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Compute gap-fill recommendations from the user's reading history."""
    # Candidate pool: every book in the catalog. rank_candidates filters
    # out the user's already-read books internally.
    candidate_ids = db.execute(select(Book.id)).scalars().all()

    ranked = rank_candidates(db, request.read_book_ids, candidate_ids)

    top = ranked[: request.top_k]
    return RecommendationResponse(
        recommendations=[
            BookResult(
                id=book.id,
                title=book.title,
                author=book.author,
                score=score,
            )
            for book, score in top
        ]
    )
