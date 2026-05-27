"""Claude-powered explanation service.

Generates 2-3 sentence grounded explanations of why a specific book is
recommended to a specific user, with a per-user daily rate limit and
content-aware caching.

Cache key: (user_id, book_id, prompt_version, read_history_hash).
The read_history_hash captures the user's current reading state so adding
more books invalidates stale cached explanations.

Rate limit: 20 new generations per user per UTC calendar day. Cache hits
do not count toward the limit (they UPDATE the existing row's
served_count and last_served_at).
"""
import hashlib
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import (
    Book,
    BookConceptAnnotation,
    Concept,
    ExplanationRequest,
)
from backend.app.services.gap_scoring import get_gap_vector


# --- Configuration ---
PROMPT_VERSION: int = 1
DAILY_LIMIT: int = 20
DEFAULT_CLAUDE_MODEL: str = "claude-haiku-4-5-20251001"
MAX_TOKENS: int = 300
TOP_GAPS_IN_PROMPT: int = 5

# Strength -> human label, matching db/migrations/001_initial_schema.sql.
STRENGTH_LABELS = {1.0: "confirmed", 0.5: "weak", 0.3: "conditional"}


SYSTEM_PROMPT = (
    "You are Atlas, a personalized reading recommender. "
    "Given a reader's top knowledge gaps and a candidate book's annotated "
    "coverage, write a 2-3 sentence explanation of why this book is "
    "recommended. Cite at least one specific gap concept and the book's "
    "strength score on that concept. Use only the data provided — do not "
    "invent information not in the input. Be informative and concise, "
    "not salesy."
)


# --- Custom exceptions the endpoint maps to HTTP codes ---
class QuotaExceededError(Exception):
    """User has used all DAILY_LIMIT new generations today."""


class ClaudeAPIError(Exception):
    """Anthropic API call failed (network, rate limit, server-side error)."""


# --- Pure helpers ---
def compute_read_history_hash(read_book_ids: list[uuid.UUID]) -> str:
    """Stable hash of a user's reading history.

    Sorts UUIDs first so the hash is order-invariant — what matters is
    which books, not what order they were logged in.
    """
    sorted_ids = sorted(str(b) for b in read_book_ids)
    joined = "|".join(sorted_ids)
    return hashlib.sha256(joined.encode()).hexdigest()


def _today_utc_start() -> datetime:
    """UTC midnight today as a tz-aware datetime."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_today_count(session: Session, user_id: uuid.UUID) -> int:
    """How many NEW generations the user has made today (UTC).

    Cache hits do not increment this; they only UPDATE existing rows.
    """
    return session.scalar(
        select(func.count(ExplanationRequest.id)).where(
            ExplanationRequest.user_id == user_id,
            ExplanationRequest.created_at >= _today_utc_start(),
        )
    ) or 0


def build_prompt(
    book: Book,
    gap_vector: dict[str, float],
    book_annotations: list[tuple[str, float]],
) -> str:
    """Construct the user-role message content for Claude."""
    top_gaps = sorted(
        ((slug, gap) for slug, gap in gap_vector.items() if gap > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )[:TOP_GAPS_IN_PROMPT]

    gap_lines = [f"  - {slug} (gap = {gap:.2f})" for slug, gap in top_gaps]
    if not gap_lines:
        gap_lines = ["  (no current gaps — user is well-covered)"]

    ann_lines = [
        f"  - {slug}: {strength:.1f} ({STRENGTH_LABELS.get(strength, 'unknown')})"
        for slug, strength in book_annotations
    ]
    if not ann_lines:
        ann_lines = ["  (no concept annotations recorded for this book)"]

    return (
        "READER'S TOP GAPS (concepts they need most):\n"
        + "\n".join(gap_lines)
        + "\n\nCANDIDATE BOOK:\n"
        + f"  Title: {book.title}\n"
        + f"  Author: {book.author}\n"
        + "  Annotated coverage:\n"
        + "\n".join(ann_lines)
        + "\n\nWrite the explanation."
    )


# --- Claude integration (the boundary tests will monkeypatch) ---
def call_claude(prompt: str, model: str | None = None) -> str:
    """Send the prompt to Claude. Returns the response text.

    Imports anthropic lazily so tests that monkeypatch this function
    never need the SDK installed.

    Raises ClaudeAPIError on any anthropic-side failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    from anthropic import Anthropic, APIError

    client = Anthropic(api_key=api_key)
    selected_model = model or os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
    try:
        response = client.messages.create(
            model=selected_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIError as e:
        raise ClaudeAPIError(str(e)) from e

    if not response.content:
        raise ClaudeAPIError("Claude returned an empty response")
    return response.content[0].text


# --- Orchestrator ---
def generate_or_get_explanation(
    session: Session,
    user_id: uuid.UUID,
    book_id: uuid.UUID,
    read_book_ids: list[uuid.UUID],
) -> tuple[ExplanationRequest, bool, int]:
    """Return (row, cached, quota_remaining).

    Caller is responsible for verifying user and book both exist (this
    function is the orchestrator, not the 404 source). It does double-
    check the book existence defensively, but does not gate on user.

    Cache hit: bumps served_count, doesn't count toward today's quota.
    Cache miss: enforces rate limit, calls Claude, inserts a new row.

    Raises:
        QuotaExceededError if today's limit would be exceeded.
        ClaudeAPIError if Claude rejects/errors.
        ValueError if the book ID can't be resolved (defensive).
    """
    hash_value = compute_read_history_hash(read_book_ids)

    cached_row = session.scalar(
        select(ExplanationRequest).where(
            ExplanationRequest.user_id == user_id,
            ExplanationRequest.book_id == book_id,
            ExplanationRequest.prompt_version == PROMPT_VERSION,
            ExplanationRequest.read_history_hash == hash_value,
        )
    )
    today_count = get_today_count(session, user_id)

    if cached_row is not None:
        cached_row.served_count += 1
        cached_row.last_served_at = datetime.now(timezone.utc)
        session.commit()
        return cached_row, True, max(0, DAILY_LIMIT - today_count)

    if today_count >= DAILY_LIMIT:
        raise QuotaExceededError(
            f"Daily limit of {DAILY_LIMIT} new explanations exceeded "
            f"for user {user_id}"
        )

    book = session.scalar(select(Book).where(Book.id == book_id))
    if book is None:
        raise ValueError(f"Book {book_id} not found")

    gap_vector = get_gap_vector(session, read_book_ids)
    book_annotations = session.execute(
        select(Concept.slug, BookConceptAnnotation.strength)
        .join(BookConceptAnnotation, BookConceptAnnotation.concept_id == Concept.id)
        .where(BookConceptAnnotation.book_id == book_id)
        .order_by(BookConceptAnnotation.strength.desc(), Concept.slug)
    ).all()

    prompt = build_prompt(book, gap_vector, book_annotations)
    model_name = os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
    explanation_text = call_claude(prompt, model=model_name)

    new_row = ExplanationRequest(
        user_id=user_id,
        book_id=book_id,
        prompt_version=PROMPT_VERSION,
        read_history_hash=hash_value,
        explanation=explanation_text,
        model=model_name,
    )
    session.add(new_row)
    session.commit()

    return new_row, False, max(0, DAILY_LIMIT - (today_count + 1))
