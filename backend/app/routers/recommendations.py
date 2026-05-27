"""Recommendation endpoints.

    POST /recommendations         — stateless: caller passes read_book_ids
    GET  /recommendations/{user_id}?strategy=gap&top_k=10
                                  — stateful: reads user_books from DB

Both call rank_candidates() under the hood. The candidate pool is every
book in the catalog; rank_candidates filters out already-read books.
"""
import uuid
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Book, User, UserBook
from backend.app.schemas import (
    BookResult,
    RecommendationRequest,
    RecommendationResponse,
)
from backend.app.services.gap_scoring import rank_candidates

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

class RecommendationStrategy(str, Enum):
    """Allowed values for the ?strategy= query param. New strategies will be
    added here as they're implemented."""
    GAP = "gap"

def _rank_to_response(
    ranked: list[tuple[Book, float]],
    top_k: int,
) -> RecommendationResponse:
    """Common conversion: list of (Book, score) -> RecommendationResponse."""
    return RecommendationResponse(
        recommendations=[
            BookResult(
                id=book.id,
                title=book.title,
                author=book.author,
                score=score,
            )
            for book, score in ranked[:top_k]
        ]
    )

# -----------------------------------------------------------------------------
# POST /recommendations  (stateless)
# -----------------------------------------------------------------------------
@router.post("", response_model=RecommendationResponse)
def recommend(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Rank candidate books against an explicit reading history."""
    candidate_ids = db.execute(select(Book.id)).scalars().all()
    ranked = rank_candidates(db, request.read_book_ids, candidate_ids)
    return _rank_to_response(ranked, request.top_k)

# -----------------------------------------------------------------------------
# GET /recommendations/{user_id}  (stateful)
# -----------------------------------------------------------------------------
@router.get("/{user_id}", response_model=RecommendationResponse)
def recommend_for_user(
    user_id: uuid.UUID,
    strategy: RecommendationStrategy = RecommendationStrategy.GAP,
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Rank candidate books against the user's stored reading history."""
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # strategy is currently only "gap"; the enum guards the value.
    # When new strategies land, branch here.
    _ = strategy

    read_book_ids = db.execute(
        select(UserBook.book_id).where(UserBook.user_id == user_id)
    ).scalars().all()

    candidate_ids = db.execute(select(Book.id)).scalars().all()
    ranked = rank_candidates(db, read_book_ids, candidate_ids)
    return _rank_to_response(ranked, top_k)
