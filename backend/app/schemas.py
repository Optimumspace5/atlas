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
