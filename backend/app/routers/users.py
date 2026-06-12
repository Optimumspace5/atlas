"""User-scoped endpoints: read history, coverage, gaps.

Three endpoints over the user_books association table:

    POST /users/{user_id}/books   — log a book as read (idempotent)
    GET  /users/{user_id}/coverage — compute coverage from logged books
    GET  /users/{user_id}/gaps     — compute gaps (sorted) from logged books

All three return 404 if the user doesn't exist. POST also 404s if the
book doesn't exist.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Book, User, UserBook
from backend.app.schemas import (
    AddBookRequest,
    CoverageResponse,
    GapEntry,
    GapsResponse,
    UserBookResponse,
)
from backend.app.schemas import (
    AddBookRequest,
    BookSearchResult,
    CoverageResponse,
    GapEntry,
    GapsResponse,
    UserBookResponse,
)
from backend.app.services.gap_scoring import get_coverage_vector, get_gap_vector


router = APIRouter(prefix="/users", tags=["users"])


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------
def _get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    """Fetch the user or raise 404."""
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user


def _get_user_book_ids(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Return all book UUIDs the user has logged."""
    return db.execute(
        select(UserBook.book_id).where(UserBook.user_id == user_id)
    ).scalars().all()
# -----------------------------------------------------------------------------
# GET /users/{user_id}/books — list books the user has logged
# -----------------------------------------------------------------------------
@router.get(
    "/{user_id}/books",
    response_model=list[BookSearchResult],
)
def list_user_books(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[Book]:
    """Return all books the user has logged, ordered by title."""
    _get_user_or_404(db, user_id)
    stmt = (
        select(Book)
        .join(UserBook, UserBook.book_id == Book.id)
        .where(UserBook.user_id == user_id)
        .order_by(Book.title)
    )
    return db.execute(stmt).scalars().all()


# -----------------------------------------------------------------------------
# POST /users/{user_id}/books — log a read book
# -----------------------------------------------------------------------------
@router.post(
    "/{user_id}/books",
    response_model=UserBookResponse,
)
def add_user_book(
    user_id: uuid.UUID,
    request: AddBookRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserBookResponse:
    """Log a book the user has read. Idempotent: 200 if already logged, 201 if new."""
    _get_user_or_404(db, user_id)

    book = db.scalar(select(Book).where(Book.id == request.book_id))
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book {request.book_id} not found",
        )

    existing = db.scalar(
        select(UserBook).where(
            UserBook.user_id == user_id,
            UserBook.book_id == request.book_id,
        )
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return UserBookResponse(user_id=existing.user_id, book_id=existing.book_id)

    new_row = UserBook(user_id=user_id, book_id=request.book_id)
    db.add(new_row)
    db.commit()
    response.status_code = status.HTTP_201_CREATED
    return UserBookResponse(user_id=new_row.user_id, book_id=new_row.book_id)
# -----------------------------------------------------------------------------
# DELETE /users/{user_id}/books/{book_id} — remove a logged book
# -----------------------------------------------------------------------------
@router.delete(
    "/{user_id}/books/{book_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_user_book(
    user_id: uuid.UUID,
    book_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Remove a book from the user's library. Idempotent: returns 204
    whether or not the book was logged (removing an absent book is a no-op)."""
    _get_user_or_404(db, user_id)
    existing = db.scalar(
        select(UserBook).where(
            UserBook.user_id == user_id,
            UserBook.book_id == book_id,
        )
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -----------------------------------------------------------------------------
# GET /users/{user_id}/coverage
# -----------------------------------------------------------------------------
@router.get(
    "/{user_id}/coverage",
    response_model=CoverageResponse,
)
def get_user_coverage(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> CoverageResponse:
    """Return the user's coverage vector over the 48 leaf concepts."""
    _get_user_or_404(db, user_id)
    book_ids = _get_user_book_ids(db, user_id)
    coverage = get_coverage_vector(db, book_ids)
    covered_count = sum(1 for v in coverage.values() if v > 0)
    return CoverageResponse(
        user_id=user_id,
        read_book_count=len(book_ids),
        covered_count=covered_count,
        coverage=coverage,
    )


# -----------------------------------------------------------------------------
# GET /users/{user_id}/gaps
# -----------------------------------------------------------------------------
@router.get(
    "/{user_id}/gaps",
    response_model=GapsResponse,
)
def get_user_gaps(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> GapsResponse:
    """Return the user's gap vector, sorted descending (biggest gaps first)."""
    _get_user_or_404(db, user_id)
    book_ids = _get_user_book_ids(db, user_id)
    gap_dict = get_gap_vector(db, book_ids)
    sorted_entries = sorted(
        (GapEntry(slug=slug, gap=gap) for slug, gap in gap_dict.items()),
        key=lambda e: e.gap,
        reverse=True,
    )
    gap_count = sum(1 for v in gap_dict.values() if v > 0)
    return GapsResponse(
        user_id=user_id,
        read_book_count=len(book_ids),
        gap_count=gap_count,
        gaps=sorted_entries,
    )
