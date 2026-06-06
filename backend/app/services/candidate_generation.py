"""Hybrid candidate generation — Stage 1 of the cross-encoder pipeline.

Combines outputs from multiple ranking strategies into a single
deduplicated candidate pool. Each candidate carries source attribution
(which retrievers surfaced it) and per-source scores (for hard-negative
typing and eval slicing).

For v1 with 3 sources (gap, embedding_read, popularity), this is a
baseline used by Phase 1 candidate recall evaluation. Phase 2 adds
gap_query_embedding as a 4th source by extending generate_candidates
with an additional pass and source key.

Phase 1.5 adds reciprocal_rank_fusion (RRF) as a non-ML rerank policy
on top of the merged pool. Used as a baseline the cross-encoder must
beat — beating raw insertion order is too easy a target.

See docs/CROSS_ENCODER_DESIGN.md Section 5 for the architectural
rationale.
"""
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book
from backend.app.services.embedding import rank_by_embedding
from backend.app.services.gap_scoring import rank_candidates
from backend.app.services.popularity import rank_by_popularity


# Source top-K defaults. Adjust here, not at call sites.
# See CROSS_ENCODER_DESIGN.md Section 5.2 for justification.
DEFAULT_TOP_K: dict[str, int] = {
    "gap": 50,
    "embedding_read": 30,
    "popularity": 30,
    # "gap_query_embedding": 50,   # Phase 2 — added when service exists
}

# Source iteration order. Determines the merged pool's insertion order
# (used as a tiebreaker for recall@K when K < union pool size).
SOURCE_ORDER: list[str] = ["gap", "embedding_read", "popularity"]


# Reciprocal Rank Fusion (RRF) weights and constant.
# Formula:  score(book) = Σ over sources of  weight_s / (RRF_K + rank_s(book))
# When source X did not surface a book, its contribution is 0 (skipped).
# The constant 60 is the standard from Cormack et al. (TREC); it dampens
# the influence of very-high-rank items so a single rank-1 hit doesn't
# dominate.
#
# Weight rationale (v1, 3 sources):
#   - gap: 1.0       — mission-aligned, the main signal
#   - popularity: 0.7 — strong baseline (current eval winner)
#   - embedding_read: 0.4 — mission-orthogonal but sharp when relevant
# Phase 2 will rebalance when gap_query_embedding is added.
RRF_K_CONSTANT = 60
RRF_WEIGHTS: dict[str, float] = {
    "gap": 1.0,
    "embedding_read": 0.4,
    "popularity": 0.7,
}


@dataclass
class Candidate:
    """One book in the candidate pool, with source attribution, scores,
    and per-source ranks (1-indexed position in that source's ordering).

    A book retrieved by multiple sources appears ONCE in the pool; its
    `sources` list grows as more sources surface it. Per-source score
    and rank fields are None when the corresponding source did not
    surface this book.
    """
    book_id: uuid.UUID
    sources: list[str] = field(default_factory=list)
    gap_score: float | None = None
    gap_rank: int | None = None
    embedding_score: float | None = None
    embedding_rank: int | None = None
    popularity_rank: int | None = None
    # gap_query_score / gap_query_rank: float | None = None   # Phase 2


def generate_candidates(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k_overrides: dict[str, int] | None = None,
) -> list[Candidate]:
    """Run all Stage 1 retrievers and merge into one deduplicated pool.

    Args:
        session: SQLAlchemy session.
        read_book_ids: the user's reading history. Each retriever filters
            these out, but we also filter at merge time for safety.
        top_k_overrides: per-source K. Falls back to DEFAULT_TOP_K. Use
            this to tune the source mix without editing the module.

    Returns:
        list[Candidate] in INSERTION ORDER (gap first, then
        embedding_read, then popularity for newly-introduced books).
        Same book retrieved by multiple sources appears ONCE; its
        sources list grows.
    """
    top_k = {**DEFAULT_TOP_K, **(top_k_overrides or {})}
    read_set = set(read_book_ids)
    pool: dict[uuid.UUID, Candidate] = {}

    # ---- Source 1: gap_scoring ----
    # rank_candidates needs a candidate_pool. Pass all book IDs; the
    # function does its own read-book filtering. For 468 books the cost
    # is trivial.
    all_book_ids = list(session.execute(select(Book.id)).scalars().all())
    gap_ranked = rank_candidates(session, read_book_ids, all_book_ids)
    rank_in_source = 0
    for book, score in gap_ranked:
        if book.id in read_set:
            continue
        rank_in_source += 1
        if rank_in_source > top_k["gap"]:
            break
        candidate = pool.setdefault(book.id, Candidate(book_id=book.id))
        candidate.sources.append("gap")
        candidate.gap_score = float(score)
        candidate.gap_rank = rank_in_source

    # ---- Source 2: embedding_read ----
    emb_ranked = rank_by_embedding(session, read_book_ids, top_k["embedding_read"])
    rank_in_source = 0
    for book, score in emb_ranked:
        if book.id in read_set:
            continue
        rank_in_source += 1
        candidate = pool.setdefault(book.id, Candidate(book_id=book.id))
        candidate.sources.append("embedding_read")
        candidate.embedding_score = float(score)
        candidate.embedding_rank = rank_in_source

    # ---- Source 3: popularity ----
    pop_ranked = rank_by_popularity(session, read_book_ids, top_k["popularity"])
    rank_in_source = 0
    for book, _score in pop_ranked:
        if book.id in read_set:
            continue
        rank_in_source += 1
        candidate = pool.setdefault(book.id, Candidate(book_id=book.id))
        candidate.sources.append("popularity")
        candidate.popularity_rank = rank_in_source

    return list(pool.values())


def reciprocal_rank_fusion(
    candidates: list[Candidate],
    weights: dict[str, float] | None = None,
    k_constant: int = RRF_K_CONSTANT,
) -> list[Candidate]:
    """Re-order candidates by Reciprocal Rank Fusion score.

    For each candidate, compute:
        score = Σ over sources of  weight_s / (k_constant + rank_in_source)

    Sources that didn't surface the candidate contribute 0 (their rank
    is None on the dataclass; we skip them).

    Returns a NEW list, sorted by RRF score descending. The original
    list is not mutated. The Candidate objects themselves are reused
    (not copied).

    Args:
        candidates: pool from generate_candidates().
        weights: per-source weight override. Falls back to RRF_WEIGHTS.
        k_constant: RRF dampening constant (default 60, Cormack et al.).

    Returns:
        New list of Candidates in RRF-ranked order, highest score first.
    """
    w = weights or RRF_WEIGHTS

    rank_attr = {
        "gap": "gap_rank",
        "embedding_read": "embedding_rank",
        "popularity": "popularity_rank",
    }

    scored: list[tuple[Candidate, float]] = []
    for c in candidates:
        score = 0.0
        for source in SOURCE_ORDER:
            rank = getattr(c, rank_attr[source], None)
            if rank is not None:
                score += w.get(source, 0.0) / (k_constant + rank)
        scored.append((c, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [c for c, _ in scored]
