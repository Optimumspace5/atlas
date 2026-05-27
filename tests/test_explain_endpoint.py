"""Tests for POST /recommendations/{user_id}/explain.

Mocks call_claude so no real Anthropic API calls happen in CI.
Exercises caching, rate limiting, hash invalidation, and error paths.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.models import Book, ExplanationRequest
from backend.app.services import explanation as explanation_service
from tests.conftest import TEST_USER_ID

CANNED_TEXT = "Mocked explanation: book covers gap concept X with strength 1.0."

@pytest.fixture
def mock_claude(monkeypatch):
    """Patch call_claude to return a fixed string; tracks call count."""
    calls = []

    def fake_call(prompt: str, model: str | None = None) -> str:
        calls.append({"prompt": prompt, "model": model})
        return CANNED_TEXT

    monkeypatch.setattr(explanation_service, "call_claude", fake_call)
    return calls

@pytest.fixture
def clean_explanations(db):
    """Wipe all explanation_requests for the test user before AND after."""
    db.query(ExplanationRequest).filter(
        ExplanationRequest.user_id == TEST_USER_ID
    ).delete()
    db.commit()
    yield
    db.query(ExplanationRequest).filter(
        ExplanationRequest.user_id == TEST_USER_ID
    ).delete()
    db.commit()

def _seed_user_books(client, book_ids):
    for bid in book_ids:
        r = client.post(
            f"/users/{TEST_USER_ID}/books",
            json={"book_id": str(bid)},
        )
        assert r.status_code in (200, 201), r.text

def test_explain_first_call_generates_and_decrements_quota(
    client, db, clean_user_books, clean_explanations, mock_claude
):
    # Set up: pick 3 annotated books, log them, pick a 4th to explain.
    from backend.app.models import BookConceptAnnotation
    read_ids = db.execute(
        select(BookConceptAnnotation.book_id).distinct().limit(3)
    ).scalars().all()
    target_id = db.execute(
        select(Book.id).where(Book.id.notin_(read_ids)).limit(1)
    ).scalar()

    _seed_user_books(client, read_ids)

    r = client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(target_id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["explanation"] == CANNED_TEXT
    assert body["cached"] is False
    assert body["quota_remaining"] == explanation_service.DAILY_LIMIT - 1
    assert len(mock_claude) == 1  # Claude was called exactly once

def test_explain_second_call_is_cached(
    client, db, clean_user_books, clean_explanations, mock_claude
):
    from backend.app.models import BookConceptAnnotation
    read_ids = db.execute(
        select(BookConceptAnnotation.book_id).distinct().limit(3)
    ).scalars().all()
    target_id = db.execute(
        select(Book.id).where(Book.id.notin_(read_ids)).limit(1)
    ).scalar()
    _seed_user_books(client, read_ids)

    # First call: generates.
    r1 = client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(target_id)},
    )
    assert r1.status_code == 200
    assert r1.json()["cached"] is False

    # Second call: same input, should hit cache.
    r2 = client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(target_id)},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["cached"] is True
    assert body2["explanation"] == r1.json()["explanation"]
    # Quota didn't decrement on cache hit.
    assert body2["quota_remaining"] == r1.json()["quota_remaining"]
    # Claude was called only once across both requests.
    assert len(mock_claude) == 1

    # served_count on the cached row should be 2.
    row = db.scalar(
        select(ExplanationRequest).where(
            ExplanationRequest.user_id == TEST_USER_ID,
            ExplanationRequest.book_id == target_id,
        )
    )
    db.refresh(row)
    assert row.served_count == 2

def test_explain_adding_book_invalidates_cache(
    client, db, clean_user_books, clean_explanations, mock_claude
):
    from backend.app.models import BookConceptAnnotation
    all_annotated = db.execute(
        select(BookConceptAnnotation.book_id).distinct().limit(5)
    ).scalars().all()
    read_ids = all_annotated[:3]
    extra_id = all_annotated[3]
    target_id = db.execute(
        select(Book.id).where(Book.id.notin_(all_annotated)).limit(1)
    ).scalar()

    _seed_user_books(client, read_ids)

    # Generate explanation #1 (cache miss).
    client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(target_id)},
    )
    assert len(mock_claude) == 1

    # Add another book — changes the read_history_hash.
    _seed_user_books(client, [extra_id])

    # Explain same book again — different hash, should be a cache miss.
    r = client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(target_id)},
    )
    assert r.status_code == 200
    assert r.json()["cached"] is False
    assert len(mock_claude) == 2  # Claude called again

def test_explain_404_for_bogus_user(client, mock_claude):
    bogus = uuid.uuid4()
    book_id = uuid.uuid4()
    r = client.post(
        f"/recommendations/{bogus}/explain",
        json={"book_id": str(book_id)},
    )
    assert r.status_code == 404
    assert "User" in r.json()["detail"]
    assert len(mock_claude) == 0

def test_explain_404_for_bogus_book(client, mock_claude):
    bogus_book = uuid.uuid4()
    r = client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(bogus_book)},
    )
    assert r.status_code == 404
    assert "Book" in r.json()["detail"]
    assert len(mock_claude) == 0

def test_explain_429_when_quota_exhausted(
    client, db, clean_user_books, clean_explanations, mock_claude
):
    from backend.app.models import BookConceptAnnotation
    read_ids = db.execute(
        select(BookConceptAnnotation.book_id).distinct().limit(2)
    ).scalars().all()
    _seed_user_books(client, read_ids)

    # Seed DAILY_LIMIT explanation rows directly (bypass the endpoint
    # to avoid burning DAILY_LIMIT real cache misses).
    candidate_books = db.execute(
        select(Book.id).where(Book.id.notin_(read_ids))
        .limit(explanation_service.DAILY_LIMIT)
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for bid in candidate_books:
        db.add(
            ExplanationRequest(
                user_id=TEST_USER_ID,
                book_id=bid,
                prompt_version=explanation_service.PROMPT_VERSION,
                read_history_hash="seeded-for-quota-test-" + str(bid)[:20],
                explanation="seed",
                model="seed-model",
                created_at=now,
                last_served_at=now,
            )
        )
    db.commit()

    # One more request — must 429.
    new_target = db.execute(
        select(Book.id)
        .where(Book.id.notin_(read_ids))
        .where(Book.id.notin_(candidate_books))
        .limit(1)
    ).scalar()
    r = client.post(
        f"/recommendations/{TEST_USER_ID}/explain",
        json={"book_id": str(new_target)},
    )
    assert r.status_code == 429
    assert r.headers.get("X-Daily-Limit") == str(explanation_service.DAILY_LIMIT)
    # Claude was never called once quota was hit.
    assert len(mock_claude) == 0
