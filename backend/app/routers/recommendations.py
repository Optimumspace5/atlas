"""Recommendation endpoints.

    POST /recommendations         — stateless: caller passes read_book_ids
    GET  /recommendations/{user_id}?strategy=hybrid&top_k=10
                                  — stateful: reads user_books from DB
    POST /recommendations/{user_id}/explain — Claude explanation for one book

Default strategy is `hybrid` (Stage 1 retrieval + RRF fusion) — the
mission-aligned production strategy (docs/EVAL_RESULTS.md Phase 6.5).
The cross-encoder is intentionally NOT exposed (offline research result).
The /explain endpoint is guarded by a per-user AND a global daily cap.
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
    ExplainRequest,
    ExplainResponse,
    RecommendationRequest,
    RecommendationResponse,
)
from backend.app.services.gap_scoring import rank_candidates
from backend.app.services.popularity import rank_by_popularity
from backend.app.services.tfidf import rank_by_tfidf
from backend.app.services.embedding import rank_by_embedding
from backend.app.services.candidate_generation import rank_by_hybrid
from backend.app.services.explanation import (
    ClaudeAPIError,
    DAILY_LIMIT,
    QuotaExceededError,
    generate_or_get_explanation,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

class RecommendationStrategy(str, Enum):
    """Allowed values for the ?strategy= query param.

    cross_encoder is deliberately excluded from the public API — it is an
    offline research result (trajectory continuation, not gap-fill), not a
    deployed strategy. See docs/EVAL_RESULTS.md Phase 5-6.
    """
    HYBRID = "hybrid"
    GAP = "gap"
    POPULARITY = "popularity"
    TFIDF = "tfidf"
    EMBEDDING = "embedding"

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
                cover_url=book.cover_url,
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
    """Rank candidate books against an explicit reading history (hybrid)."""
    ranked = rank_by_hybrid(db, list(request.read_book_ids), request.top_k)
    return _rank_to_response(ranked, request.top_k)

# -----------------------------------------------------------------------------
# GET /recommendations/{user_id}  (stateful)
# -----------------------------------------------------------------------------
@router.get("/{user_id}", response_model=RecommendationResponse)
def recommend_for_user(
    user_id: uuid.UUID,
    strategy: RecommendationStrategy = RecommendationStrategy.HYBRID,
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

    read_book_ids = db.execute(
        select(UserBook.book_id)
        .where(UserBook.user_id == user_id)
        .order_by(UserBook.created_at.desc())
    ).scalars().all()

    if strategy == RecommendationStrategy.GAP:
        candidate_ids = db.execute(select(Book.id)).scalars().all()
        ranked = rank_candidates(db, read_book_ids, candidate_ids)
    elif strategy == RecommendationStrategy.POPULARITY:
        ranked = rank_by_popularity(db, read_book_ids, top_k)
    elif strategy == RecommendationStrategy.TFIDF:
        ranked = rank_by_tfidf(db, read_book_ids, top_k)
    elif strategy == RecommendationStrategy.EMBEDDING:
        ranked = rank_by_embedding(db, read_book_ids, top_k)
    else:  # RecommendationStrategy.HYBRID — the mission-aligned default
        ranked = rank_by_hybrid(db, list(read_book_ids), top_k)

    return _rank_to_response(ranked, top_k)


# -----------------------------------------------------------------------------
# POST /recommendations/{user_id}/explain
# -----------------------------------------------------------------------------
@router.post("/{user_id}/explain", response_model=ExplainResponse)
def explain_recommendation(
    user_id: uuid.UUID,
    request: ExplainRequest,
    db: Session = Depends(get_db),
) -> ExplainResponse:
    """Generate (or return cached) Claude explanation for one book.

    Two guards: a per-user daily limit and an app-wide global daily cap.
    Cache hits return immediately and count toward neither.
    """
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    book = db.scalar(select(Book).where(Book.id == request.book_id))
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book {request.book_id} not found",
        )

    read_book_ids = db.execute(
        select(UserBook.book_id).where(UserBook.user_id == user_id)
    ).scalars().all()

    try:
        row, cached, quota_remaining = generate_or_get_explanation(
            session=db,
            user_id=user_id,
            book_id=request.book_id,
            read_book_ids=read_book_ids,
        )
    except QuotaExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
            headers={"X-Daily-Limit": str(DAILY_LIMIT)},
        )
    except ClaudeAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude API failure: {e}",
        )
    except RuntimeError as e:
        # ANTHROPIC_API_KEY missing — server misconfiguration.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    return ExplainResponse(
        book_id=row.book_id,
        explanation=row.explanation,
        model=row.model,
        cached=cached,
        quota_remaining=quota_remaining,
    )
