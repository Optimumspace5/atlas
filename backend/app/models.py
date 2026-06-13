"""SQLAlchemy ORM models for Atlas.

These models mirror the schema defined in db/migrations/001_initial_schema.sql
and the subsequent Alembic migrations under backend/alembic/versions/.

The SQL files are the source of truth for the database schema. These ORM
classes exist so application code can read/write rows via the SQLAlchemy
session API without hand-writing every query.

NOTE: Book.embedding is intentionally NOT declared here. The pgvector
extension is enabled in the database but no embedding column exists on
the books table yet. The embedder pipeline will add both the column
(via a new Alembic migration) and the corresponding Mapped[] field
at the same time.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Integer, Float, Date, UniqueConstraint, text
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

class Base(DeclarativeBase):
    """Shared declarative base. All models inherit from this so they share
    the same MetaData object (required for Alembic autogenerate and for
    Base.metadata.create_all in tests)."""
    pass

class Book(Base):
    __tablename__ = "books"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    author: Mapped[str] = mapped_column(String(512), nullable=False)
    isbn_13: Mapped[str | None] = mapped_column(String(13), unique=True, nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="manual"
    )
    curated: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    difficulty_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("level IN (0, 1)", name="concepts_level_check"),
    )

class BookConceptAnnotation(Base):
    __tablename__ = "book_concept_annotations"

    book_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        primary_key=True,
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    annotation_type: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        nullable=False,
        server_default="manual",
    )
    strength: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "strength IN (1.0, 0.5, 0.3)",
            name="book_concept_annotations_strength_check",
        ),
    )

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

class UserBook(Base):
    __tablename__ = "user_books"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date_read: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

class ExplanationRequest(Base):
    __tablename__ = "explanation_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    read_history_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    served_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    last_served_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "book_id",
            "prompt_version",
            "read_history_hash",
            name="explanation_requests_cache_key_unique",
        ),
    )

class BookEmbedding(Base):
    """One 384-dim sentence embedding per book.

    Dimension locked at 384 to match BAAI/bge-small-en-v1.5. Switching to a
    768-dim model requires a new migration that alters or recreates this
    column.
    """
    __tablename__ = "book_embeddings"

    book_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class ConceptEmbedding(Base):
    """One 384-dim sentence embedding per concept.

    Used by gap_query_embedding service: at query time, fetch the
    user's top gap concepts' vectors, weight-sum by gap value, and
    find books with embeddings nearest to the resulting query vector.

    Dimension locked at 384 to match BAAI/bge-small-en-v1.5 (same
    model as book_embeddings). Switching to a 768-dim model requires
    a new migration.
    """
    __tablename__ = "concept_embeddings"

    concept_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
