"""Phase 1 end-to-end integration test.

Exercises the full read path: seed user -> log books -> read coverage/gaps
-> request recommendations both ways -> verify stateless and stateful
recommendations agree.
"""
import uuid

from sqlalchemy import select

from backend.app.models import BookConceptAnnotation
from tests.conftest import TEST_USER_ID


def test_phase1_end_to_end(client, db, clean_user_books):
    # --- Pick 3 sample book UUIDs that have annotations ---
    sample_ids = db.execute(
        select(BookConceptAnnotation.book_id).distinct().limit(3)
    ).scalars().all()
    sample_ids_str = [str(b) for b in sample_ids]
    assert len(sample_ids) == 3, "Test corpus must have at least 3 annotated books"

    # --- 1. POST 3 books to user_books ---
    for bid in sample_ids_str:
        r = client.post(
            f"/users/{TEST_USER_ID}/books",
            json={"book_id": bid},
        )
        assert r.status_code == 201, f"POST failed: {r.status_code} {r.text}"

    # --- 2. GET coverage ---
    r = client.get(f"/users/{TEST_USER_ID}/coverage")
    assert r.status_code == 200
    cov = r.json()
    assert cov["read_book_count"] == 3
    assert len(cov["coverage"]) == 48, "Coverage vector must have 48 leaf entries"
    assert cov["covered_count"] >= 1, "3 annotated books must cover at least 1 concept"

    # --- 3. GET gaps ---
    r = client.get(f"/users/{TEST_USER_ID}/gaps")
    assert r.status_code == 200
    gaps = r.json()
    assert gaps["read_book_count"] == 3
    assert len(gaps["gaps"]) == 48
    # Sorted descending by gap
    gap_values = [g["gap"] for g in gaps["gaps"]]
    assert gap_values == sorted(gap_values, reverse=True), "Gaps must be sorted desc"

    # --- 4. POST /recommendations (stateless) ---
    r = client.post(
        "/recommendations",
        json={"read_book_ids": sample_ids_str, "top_k": 10},
    )
    assert r.status_code == 200
    stateless = r.json()
    assert len(stateless["recommendations"]) > 0

    # --- 5. GET /recommendations/{user_id} (stateful) ---
    r = client.get(f"/recommendations/{TEST_USER_ID}?strategy=gap&top_k=10")
    assert r.status_code == 200
    stateful = r.json()

    # --- 6. The load-bearing assertion: stateless == stateful ---
    stateless_titles = [b["title"] for b in stateless["recommendations"]]
    stateful_titles = [b["title"] for b in stateful["recommendations"]]
    assert stateless_titles == stateful_titles, (
        f"Stateless and stateful recommendations diverged:\n"
        f"  stateless: {stateless_titles}\n"
        f"  stateful:  {stateful_titles}"
    )

    # --- 7. Sanity 404s ---
    bogus_user = uuid.uuid4()
    r = client.get(f"/recommendations/{bogus_user}")
    assert r.status_code == 404
    r = client.get(f"/users/{bogus_user}/coverage")
    assert r.status_code == 404
    r = client.post(f"/users/{TEST_USER_ID}/books", json={"book_id": str(uuid.uuid4())})
    assert r.status_code == 404, "POST with bogus book should 404"
