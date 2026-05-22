"""Gap scoring — compute coverage vectors over the taxonomy.

Given a user's reading history (a list of book UUIDs), produces a coverage
vector: a dict mapping each leaf concept slug to an aggregated strength score.

Aggregation: sum of strengths across the user's books for that concept.
    - 1 confirmed (1.0) book                = 1.0
    - 3 weak (0.5) books                    = 1.5
    - 1 confirmed + 1 weak                  = 1.5
    - 0 annotated books on that concept     = 0.0

Output shape: every level=1 (leaf) concept appears as a key, with value 0.0
for concepts the user has no annotated coverage on. This stable shape lets
downstream callers (ranking, recommendation, evaluation) avoid 'missing key
= uncovered' bugs.

This module is pure business logic. It opens no sessions and reads no env
vars — the caller (FastAPI endpoint, script, test) passes in an active
SQLAlchemy session.
"""
import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.app.models import BookConceptAnnotation, Concept


def get_coverage_vector(
    session: Session,
    book_ids: list[uuid.UUID],
) -> dict[str, float]:
    """Return {leaf_slug: aggregated_strength} for every level=1 concept.

    Args:
        session: active SQLAlchemy session bound to the Atlas DB.
        book_ids: UUIDs of books the user has read. Duplicates are deduped.
            An empty list returns an all-zero vector.

    Returns:
        Dict with one key per leaf concept slug (48 entries in v1).
        Concepts the user has no annotations on get a score of 0.0.
    """
    # Dedupe to avoid double-counting if the caller passes the same book twice.
    unique_ids = list(set(book_ids))

    # Build the query: every level=1 concept on the left, with a LEFT JOIN
    # to annotations restricted to the user's books. The filter on book_id
    # MUST live in the JOIN condition (not WHERE) so concepts the user has
    # no annotations on still produce a row (with SUM = NULL -> coerced to 0).
    join_condition = and_(
        BookConceptAnnotation.concept_id == Concept.id,
        BookConceptAnnotation.book_id.in_(unique_ids) if unique_ids else False,
    )

    stmt = (
        select(
            Concept.slug,
            func.coalesce(func.sum(BookConceptAnnotation.strength), 0.0).label("score"),
        )
        .select_from(Concept)
        .outerjoin(BookConceptAnnotation, join_condition)
        .where(Concept.level == 1)
        .group_by(Concept.slug)
    )

    rows = session.execute(stmt).all()
    return {slug: float(score) for slug, score in rows}


# -----------------------------------------------------------------------------
# Ad-hoc smoke test — runs when this file is executed directly.
# Picks a handful of books that have annotations and prints their coverage
# vector. Not a real test suite; just a sanity check that the function works
# end-to-end against the live DB.
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    # Allow running this file directly via `python backend/app/services/gap_scoring.py`
    REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
    sys.path.insert(0, str(REPO_ROOT))

    from sqlalchemy import create_engine

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(2)

    engine = create_engine(db_url)
    with Session(engine) as session:
        # Pick 5 books that actually have annotations.
        sample_ids = session.execute(
            select(BookConceptAnnotation.book_id).distinct().limit(5)
        ).scalars().all()

        print(f"Using {len(sample_ids)} sample book IDs")
        print()

        vec = get_coverage_vector(session, sample_ids)

        print(f"Vector size: {len(vec)} (expected 48)")
        print()

        # Show top 10 covered + bottom 10 uncovered for a sanity readout.
        sorted_items = sorted(vec.items(), key=lambda kv: kv[1], reverse=True)

        print("Top 10 covered concepts:")
        for slug, score in sorted_items[:10]:
            print(f"  {score:>5.1f}  {slug}")

        print()
        print("Bottom 10 (gaps):")
        for slug, score in sorted_items[-10:]:
            print(f"  {score:>5.1f}  {slug}")

        # Invariants
        zero_count = sum(1 for v in vec.values() if v == 0.0)
        nonzero_count = len(vec) - zero_count
        print()
        print(f"Zero scores: {zero_count}")
        print(f"Non-zero scores: {nonzero_count}")
        assert len(vec) == 48, f"Expected 48 leaves, got {len(vec)}"
        print("OK: vector has 48 leaves")
