"""Gap scoring — coverage, gap, and candidate ranking over the taxonomy.

The recommender's core math lives here. Four functions form the pipeline:

    1. get_coverage_vector(book_ids) -> {slug: score}
       How well the user knows each concept, based on books read.
       Aggregation: sum of strengths.

    2. get_gap_vector(book_ids) -> {slug: gap}
       Complement of coverage. gap = max(0, COVERAGE_TARGET - coverage).
       Concepts at or above target get 0; below target get (target - coverage).

    3. score_candidate(candidate_book_id, gap_vector) -> float
       How useful one candidate book is for filling the user's gaps.
       Score = sum over concepts of min(candidate_strength, user_gap).
       Capped: a strong book on a small gap can't 'over-fill' it.

    4. rank_candidates(read_book_ids, candidate_pool) -> [(Book, score), ...]
       Full pipeline. Automatically filters already-read books from the pool.
       Returns Book ORM objects with scores, sorted descending.

This module is pure business logic. It opens no sessions and reads no env
vars — the caller (FastAPI endpoint, script, test) passes in an active
SQLAlchemy session.
"""
import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.app.models import Book, BookConceptAnnotation, Concept


# Coverage above this threshold is treated as 'adequately covered' (gap=0).
# Calibrated for sum-of-strengths semantics; roughly equivalent to 2 confirmed
# books or 4 weak books. Tune this knob to make recommendations more or less
# demanding.
COVERAGE_TARGET: float = 2.0


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
    unique_ids = list(set(book_ids))

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


def get_gap_vector(
    session: Session,
    book_ids: list[uuid.UUID],
) -> dict[str, float]:
    """Return {leaf_slug: gap_score} where gap = max(0, TARGET - coverage).

    Args:
        session: active SQLAlchemy session.
        book_ids: UUIDs of books the user has read.

    Returns:
        Dict with one key per leaf concept slug. Concepts the user has fully
        covered get gap=0; concepts they haven't touched get gap=TARGET.
        Values are always in the range [0, COVERAGE_TARGET].
    """
    coverage = get_coverage_vector(session, book_ids)
    return {
        slug: max(0.0, COVERAGE_TARGET - score)
        for slug, score in coverage.items()
    }


def score_candidate(
    session: Session,
    candidate_book_id: uuid.UUID,
    gap_vector: dict[str, float],
) -> float:
    """Score how much reading one book would reduce the user's gaps.

    For each concept the candidate book is annotated on, the contribution is
    min(candidate_strength, user_gap_on_concept). Contributions sum across
    concepts.

    Capping at the remaining gap is honest: reading a strong book on a
    nearly-covered concept can only 'fill' as much of the gap as is left.
    A confirmed (1.0) book on a 0.5 gap contributes 0.5, not 1.0.

    Args:
        session: active SQLAlchemy session.
        candidate_book_id: the book to score.
        gap_vector: the user's current gap vector (from get_gap_vector).

    Returns:
        A non-negative float. Books unrelated to any current gap return 0.0.
    """
    stmt = (
        select(Concept.slug, BookConceptAnnotation.strength)
        .join(BookConceptAnnotation, BookConceptAnnotation.concept_id == Concept.id)
        .where(BookConceptAnnotation.book_id == candidate_book_id)
    )
    rows = session.execute(stmt).all()

    score = 0.0
    for slug, strength in rows:
        gap = gap_vector.get(slug, 0.0)
        score += min(float(strength), gap)
    return score


def rank_candidates(
    session: Session,
    read_book_ids: list[uuid.UUID],
    candidate_pool: list[uuid.UUID],
) -> list[tuple[Book, float]]:
    """Rank candidate books by how much they would fill the user's gaps.

    Pipeline:
        coverage(read) -> gap(read) -> score each candidate -> sort desc.

    Args:
        session: active SQLAlchemy session.
        read_book_ids: UUIDs of books the user has already read.
        candidate_pool: UUIDs of books to rank. Books also present in
            read_book_ids are automatically filtered out.

    Returns:
        List of (Book ORM object, score) tuples, sorted by score descending.
    """
    read_set = set(read_book_ids)
    pool = [bid for bid in candidate_pool if bid not in read_set]
    if not pool:
        return []

    gap_vector = get_gap_vector(session, read_book_ids)

    candidates = session.execute(
        select(Book).where(Book.id.in_(pool))
    ).scalars().all()

    scored = [
        (book, score_candidate(session, book.id, gap_vector))
        for book in candidates
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


# -----------------------------------------------------------------------------
# End-to-end smoke test — runs the full pipeline against the live DB.
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
    sys.path.insert(0, str(REPO_ROOT))

    from sqlalchemy import create_engine

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(2)

    engine = create_engine(db_url)
    with Session(engine) as session:
        # ---- Sample reading history (5 annotated books) ----
        read_ids = session.execute(
            select(BookConceptAnnotation.book_id).distinct().limit(5)
        ).scalars().all()

        read_books = session.execute(
            select(Book).where(Book.id.in_(read_ids))
        ).scalars().all()

        print(f"=== Reading history: {len(read_books)} books ===")
        for b in read_books:
            print(f"  - {b.title} — {b.author}")
        print()

        # ---- Step 1: coverage ----
        coverage = get_coverage_vector(session, read_ids)
        covered = sum(1 for v in coverage.values() if v > 0)
        top5_cov = sorted(coverage.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print(f"=== Coverage: {covered}/48 leaves covered ===")
        for slug, s in top5_cov:
            print(f"  {s:>5.1f}  {slug}")
        print()

        # ---- Step 2: gap ----
        gap = get_gap_vector(session, read_ids)
        top5_gaps = sorted(gap.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print(f"=== Top 5 gaps (target={COVERAGE_TARGET}) ===")
        for slug, g in top5_gaps:
            print(f"  {g:>5.2f}  {slug}")
        assert all(0.0 <= g <= COVERAGE_TARGET for g in gap.values()), \
            "Gap values must be in [0, TARGET]"
        print("OK: all gaps in [0, TARGET]")
        print()

        # ---- Step 3: score 3 individual candidates ----
        candidate_ids = session.execute(
            select(Book.id).where(Book.id.notin_(read_ids)).limit(3)
        ).scalars().all()
        print("=== Scoring 3 candidate books ===")
        for cid in candidate_ids:
            book = session.scalar(select(Book).where(Book.id == cid))
            s = score_candidate(session, cid, gap)
            print(f"  {s:>5.2f}  {book.title} — {book.author}")
        print()

        # ---- Step 4: rank the full pool ----
        all_book_ids = session.execute(select(Book.id)).scalars().all()
        ranked = rank_candidates(session, read_ids, all_book_ids)
        print(f"=== Top 10 recommendations (pool size: {len(ranked)}) ===")
        for book, score in ranked[:10]:
            print(f"  {score:>5.2f}  {book.title} — {book.author}")
        print()

        # ---- Invariants ----
        assert len(coverage) == 48, f"Expected 48 coverage entries, got {len(coverage)}"
        assert len(gap) == 48, f"Expected 48 gap entries, got {len(gap)}"
        read_set = set(read_ids)
        assert all(b.id not in read_set for b, _ in ranked), \
            "Ranked list must exclude already-read books"
        assert all(ranked[i][1] >= ranked[i + 1][1] for i in range(len(ranked) - 1)), \
            "Ranked list must be sorted descending"
        print("OK: all invariants hold")
