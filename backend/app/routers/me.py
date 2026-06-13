"""Authenticated per-user endpoints (/me/...).

The user is derived from the Supabase JWT (Depends(get_current_user)),
never from a URL parameter — the secure replacement for the old
/users/{user_id}/... and /recommendations/{user_id} routes.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import Book, User, UserBook
from backend.app.schemas import (
    AddBookRequest,
    BookSearchResult,
    CoverageResponse,
    ExplainRequest,
    ExplainResponse,
    GapEntry,
    GapsResponse,
    RecommendationResponse,
    RoadmapResponse,
    UserBookResponse,
)
from backend.app.services.gap_scoring import get_coverage_vector, get_gap_vector
from backend.app.services.explanation import (
    ClaudeAPIError,
    DAILY_LIMIT,
    QuotaExceededError,
    generate_or_get_explanation,
)
from backend.app.routers.recommendations import (
    RecommendationStrategy,
    rank_for_strategy,
    rank_to_response,
)
from backend.app.services.roadmaps import VALID_ROLES, build_roadmap

router = APIRouter(prefix="/me", tags=["me"])


def _read_book_ids(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """User's logged book ids, recency-first."""
    return db.execute(
        select(UserBook.book_id)
        .where(UserBook.user_id == user_id)
        .order_by(UserBook.created_at.desc())
    ).scalars().all()


# ---- identity ----
@router.get("")
def read_me(user: User = Depends(get_current_user)) -> dict:
    return {"id": str(user.id), "email": user.email}


# ---- library ----
@router.get("/books", response_model=list[BookSearchResult])
def list_books(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(Book)
        .join(UserBook, UserBook.book_id == Book.id)
        .where(UserBook.user_id == user.id)
        .order_by(Book.title)
    ).scalars().all()


@router.post("/books", response_model=UserBookResponse)
def add_book(
    request: AddBookRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserBookResponse:
    book = db.scalar(select(Book).where(Book.id == request.book_id))
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {request.book_id} not found")
    existing = db.scalar(
        select(UserBook).where(
            UserBook.user_id == user.id,
            UserBook.book_id == request.book_id,
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return UserBookResponse(user_id=existing.user_id, book_id=existing.book_id)
    row = UserBook(user_id=user.id, book_id=request.book_id)
    db.add(row)
    db.commit()
    response.status_code = status.HTTP_201_CREATED
    return UserBookResponse(user_id=row.user_id, book_id=row.book_id)


@router.delete("/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_book(
    book_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    existing = db.scalar(
        select(UserBook).where(
            UserBook.user_id == user.id,
            UserBook.book_id == book_id,
        )
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- coverage / gaps ----
@router.get("/coverage", response_model=CoverageResponse)
def coverage(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverageResponse:
    book_ids = _read_book_ids(db, user.id)
    cov = get_coverage_vector(db, book_ids)
    return CoverageResponse(
        user_id=user.id,
        read_book_count=len(book_ids),
        covered_count=sum(1 for v in cov.values() if v > 0),
        coverage=cov,
    )


@router.get("/gaps", response_model=GapsResponse)
def gaps(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GapsResponse:
    book_ids = _read_book_ids(db, user.id)
    gap_dict = get_gap_vector(db, book_ids)
    sorted_entries = sorted(
        (GapEntry(slug=slug, gap=gap) for slug, gap in gap_dict.items()),
        key=lambda e: e.gap,
        reverse=True,
    )
    return GapsResponse(
        user_id=user.id,
        read_book_count=len(book_ids),
        gap_count=sum(1 for v in gap_dict.values() if v > 0),
        gaps=sorted_entries,
    )


# ---- recommendations ----
@router.get("/recommendations", response_model=RecommendationResponse)
def recommendations(
    strategy: RecommendationStrategy = RecommendationStrategy.HYBRID,
    top_k: int = Query(10, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    read_book_ids = _read_book_ids(db, user.id)
    ranked = rank_for_strategy(db, strategy, read_book_ids, top_k)
    return rank_to_response(ranked, top_k)


@router.post("/recommendations/explain", response_model=ExplainResponse)
def explain(
    request: ExplainRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExplainResponse:
    book = db.scalar(select(Book).where(Book.id == request.book_id))
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {request.book_id} not found")
    read_book_ids = _read_book_ids(db, user.id)
    try:
        row, cached, quota_remaining = generate_or_get_explanation(
            session=db,
            user_id=user.id,
            book_id=request.book_id,
            read_book_ids=read_book_ids,
        )
    except QuotaExceededError as e:
        raise HTTPException(
            status_code=429, detail=str(e), headers={"X-Daily-Limit": str(DAILY_LIMIT)}
        )
    except ClaudeAPIError as e:
        raise HTTPException(status_code=502, detail=f"Claude API failure: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ExplainResponse(
        book_id=row.book_id,
        explanation=row.explanation,
        model=row.model,
        cached=cached,
        quota_remaining=quota_remaining,
    )


# ---- roadmaps ----
@router.get("/roadmaps/{role}", response_model=RoadmapResponse)
def roadmap(
    role: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoadmapResponse:
    """Ordered learning journey for a role (investor|trader), with the caller's
    read books flagged and the first unread rung marked as 'next'."""
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown roadmap role {role!r}. Valid: {', '.join(VALID_ROLES)}",
        )
    read_ids = set(_read_book_ids(db, user.id))
    return build_roadmap(db, role, read_ids)
