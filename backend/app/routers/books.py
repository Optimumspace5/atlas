"""Book endpoints — search the v1 catalog."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Book
from backend.app.schemas import BookSearchResult


router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=list[BookSearchResult])
def search_books(
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[BookSearchResult]:
    """Search books by title or author (case-insensitive substring match).

    Returns up to `limit` matches ordered alphabetically by title.
    """
    pattern = f"%{q}%"
    stmt = (
        select(Book)
        .where(
            Book.curated.is_(True),
            or_(Book.title.ilike(pattern), Book.author.ilike(pattern)),
        )
        .order_by(Book.title)
        .limit(limit)
    )
    return db.execute(stmt).scalars().all()
