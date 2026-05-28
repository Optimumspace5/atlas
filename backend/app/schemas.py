"""Pydantic schemas — the request and response shapes for the Atlas API.

These classes do three jobs:
    1. Validate incoming JSON (wrong types or missing fields -> 422 error)
    2. Serialize outgoing data to JSON (UUIDs -> strings, etc.)
    3. Power the auto-generated /docs page (FastAPI reads field metadata)

Schemas live separately from the ORM models in models.py. The ORM models
describe the database; schemas describe what the API exposes. Keeping them
distinct lets us evolve the API surface without changing the DB, and vice
versa.
"""
import uuid

from pydantic import BaseModel, ConfigDict, Field


class RecommendationRequest(BaseModel):
    """Input to POST /recommendations."""

    read_book_ids: list[uuid.UUID] = Field(
        ...,
        description="UUIDs of books the user has already read.",
    )
    top_k: int = Field(
        10,
        ge=1,
        le=100,
        description="How many top-scored recommendations to return. Default 10.",
    )


class BookResult(BaseModel):
    """One ranked recommendation in the response list."""

    id: uuid.UUID
    title: str
    author: str
    cover_url: str | None
    score: float = Field(
        ...,
        description="Gap-fill score from rank_candidates(). Higher = better fit.",
    )

    # Allow building this schema from ORM objects (Book has .id, .title, .author
    # as attributes, not dict keys). Required by FastAPI's response_model.
    model_config = ConfigDict(from_attributes=True)


class RecommendationResponse(BaseModel):
    """Output from POST /recommendations."""

    recommendations: list[BookResult] = Field(
        ...,
        description="Ranked list, highest score first. Length <= top_k.",
    )

    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------------------------------
# Schemas for /users/{user_id}/* endpoints
# -----------------------------------------------------------------------------
class AddBookRequest(BaseModel):
    """Body of POST /users/{user_id}/books."""

    book_id: uuid.UUID = Field(..., description="UUID of the book to log as read.")


class UserBookResponse(BaseModel):
    """One row from user_books, returned by POST /users/{user_id}/books."""

    user_id: uuid.UUID
    book_id: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class CoverageResponse(BaseModel):
    """Body of GET /users/{user_id}/coverage."""

    user_id: uuid.UUID
    read_book_count: int = Field(..., description="How many books the user has logged.")
    covered_count: int = Field(..., description="Number of concepts with coverage > 0.")
    coverage: dict[str, float] = Field(
        ...,
        description="48-key dict: leaf concept slug -> sum-of-strengths coverage score.",
    )


class GapEntry(BaseModel):
    """One entry in the sorted gap list."""

    slug: str
    gap: float


class GapsResponse(BaseModel):
    """Body of GET /users/{user_id}/gaps."""

    user_id: uuid.UUID
    read_book_count: int
    gap_count: int = Field(..., description="Number of concepts with gap > 0.")
    gaps: list[GapEntry] = Field(
        ...,
        description="Sorted by gap descending. Top entries are biggest learning opportunities.",
    )

# -----------------------------------------------------------------------------
# Schemas for POST /recommendations/{user_id}/explain
# -----------------------------------------------------------------------------
class ExplainRequest(BaseModel):
    """Body of POST /recommendations/{user_id}/explain."""

    book_id: uuid.UUID = Field(
        ...,
        description="UUID of the book to explain.",
    )


class ExplainResponse(BaseModel):
    """Body returned by POST /recommendations/{user_id}/explain."""

    book_id: uuid.UUID
    explanation: str = Field(
        ...,
        description="2-3 sentence grounded explanation produced by Claude.",
    )
    model: str = Field(
        ...,
        description="Claude model ID that produced (or originally produced) the explanation.",
    )
    cached: bool = Field(
        ...,
        description="True if returned from cache; cache hits do not count toward quota.",
    )
    quota_remaining: int = Field(
        ...,
        description="New generations remaining for this user today (UTC). Cached hits don't decrement.",
    )

    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------------------------------
# Schemas for GET /books
# -----------------------------------------------------------------------------
class BookSearchResult(BaseModel):
    """One result row from GET /books?q=..."""

    id: uuid.UUID
    title: str
    author: str
    isbn_13: str | None
    publication_year: int | None
    cover_url: str | None

    model_config = ConfigDict(from_attributes=True)

# -----------------------------------------------------------------------------
# Schemas for GET /concepts
# -----------------------------------------------------------------------------
class ConceptLeaf(BaseModel):
    """One leaf concept (level=1)."""

    slug: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class ConceptParent(BaseModel):
    """One parent category (level=0) with its leaf concepts nested."""

    slug: str
    name: str
    leaves: list[ConceptLeaf]
