"""Recommendation endpoints.

    POST /recommendations  — stateless: caller passes read_book_ids (hybrid)

Per-user recommendation + explain routes now live under /me
(routers/me.py); the user is derived from the auth token, not the URL.
This module also exports the strategy enum + dispatch/response helpers
that /me reuses.
"""
import uuid
from enum import Enum

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
from backend.app.services.popularity import rank_by_popularity
from backend.app.services.tfidf import rank_by_tfidf
from backend.app.services.embedding import rank_by_embedding
from backend.app.services.candidate_generation import rank_by_hybrid

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class RecommendationStrategy(str, Enum):
    """Public strategies. cross_encoder is intentionally excluded (offline
    research result, not a deployed strategy)."""
    HYBRID = "hybrid"
    GAP = "gap"
    POPULARITY = "popularity"
    TFIDF = "tfidf"
    EMBEDDING = "embedding"


def rank_for_strategy(
    db: Session,
    strategy: RecommendationStrategy,
    read_book_ids: list[uuid.UUID],
    top_k: int,
) -> list[tuple[Book, float]]:
    """Dispatch a strategy to its ranking function. Shared by the stateless
    endpoint and the authenticated /me/recommendations route."""
    if strategy == RecommendationStrategy.GAP:
        candidate_ids = db.execute(
            select(Book.id).where(Book.curated.is_(True))
        ).scalars().all()
        return rank_candidates(db, read_book_ids, candidate_ids)
    if strategy == RecommendationStrategy.POPULARITY:
        return rank_by_popularity(db, read_book_ids, top_k)
    if strategy == RecommendationStrategy.TFIDF:
        return rank_by_tfidf(db, read_book_ids, top_k)
    if strategy == RecommendationStrategy.EMBEDDING:
        return rank_by_embedding(db, read_book_ids, top_k)
    return rank_by_hybrid(db, list(read_book_ids), top_k)  # HYBRID default


def rank_to_response(
    ranked: list[tuple[Book, float]],
    top_k: int,
) -> RecommendationResponse:
    """Convert (Book, score) list into the API response shape."""
    return RecommendationResponse(
        recommendations=[
            BookResult(
                id=book.id,
                title=book.title,
                author=book.author,
                cover_url=book.cover_url,
                score=score,
            )
            for book, score in ranked[:top_k]
        ]
    )


@router.post("", response_model=RecommendationResponse)
def recommend(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Stateless: rank against an explicit reading history (hybrid)."""
    ranked = rank_by_hybrid(db, list(request.read_book_ids), request.top_k)
    return rank_to_response(ranked, request.top_k)
