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

    # --- 6b. GET /recommendations/{user_id}?strategy=popularity ---
    r = client.get(f"/recommendations/{TEST_USER_ID}?strategy=popularity&top_k=10")
    assert r.status_code == 200
    popular = r.json()
    assert len(popular["recommendations"]) > 0
    assert all(isinstance(b["score"], (int, float)) for b in popular["recommendations"])
    # Popularity scores are annotation counts — monotonically non-increasing.
    popular_scores = [b["score"] for b in popular["recommendations"]]
    assert popular_scores == sorted(popular_scores, reverse=True), \
        "Popularity recommendations must be sorted by score desc"
    # Already-read books must be excluded from popularity too.
    popular_ids = {b["id"] for b in popular["recommendations"]}
    assert all(read_id not in popular_ids for read_id in sample_ids_str), \
        "Popularity must exclude already-read books"
    # --- 6c. GET /recommendations/{user_id}?strategy=tfidf ---
    r = client.get(f"/recommendations/{TEST_USER_ID}?strategy=tfidf&top_k=10")
    assert r.status_code == 200
    tfidf = r.json()
    assert len(tfidf["recommendations"]) > 0
    assert all(isinstance(b["score"], (int, float)) for b in tfidf["recommendations"])
    # TF-IDF scores are cosine similarities — sorted desc.
    tfidf_scores = [b["score"] for b in tfidf["recommendations"]]
    assert tfidf_scores == sorted(tfidf_scores, reverse=True), \
        "TF-IDF recommendations must be sorted by score desc"
    # All scores in [0, 1] since TF-IDF vectors are non-negative.
    assert all(0.0 <= s <= 1.0 for s in tfidf_scores), \
        "Cosine similarity must be in [0, 1]"
    # Already-read books excluded.
    tfidf_ids = {b["id"] for b in tfidf["recommendations"]}
    assert all(read_id not in tfidf_ids for read_id in sample_ids_str), \
        "TF-IDF must exclude already-read books"

    # --- 6d. Invalid strategy is rejected by the enum guard ---
    r = client.get(f"/recommendations/{TEST_USER_ID}?strategy=bogus")
    assert r.status_code == 422, "Unknown strategy should 422 via enum validation"

    # --- 7. Sanity 404s ---
    bogus_user = uuid.uuid4()
    r = client.get(f"/recommendations/{bogus_user}")
    assert r.status_code == 404
    r = client.get(f"/users/{bogus_user}/coverage")
    assert r.status_code == 404
    r = client.post(f"/users/{TEST_USER_ID}/books", json={"book_id": str(uuid.uuid4())})
    assert r.status_code == 404, "POST with bogus book should 404"
