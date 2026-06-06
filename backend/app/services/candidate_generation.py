"""Hybrid candidate generation — Stage 1 of the cross-encoder pipeline.

Combines outputs from multiple ranking strategies into a single
deduplicated candidate pool. Each candidate carries source attribution
(which retrievers surfaced it) and per-source scores (for hard-negative
typing and eval slicing).

Phase 2 brings the source count to four:
    - gap_scoring        (50)  : mission-aligned, annotation-bound
    - gap_query_embedding (50) : mission-aligned, bypasses annotation sparsity
    - embedding_read     (30)  : semantic breadth (mission-orthogonal)
    - popularity         (30)  : broad-coverage fallback

After deduplication, the pool is reordered by Stage 1b RRF rank fusion
(reciprocal_rank_fusion below) before the cross-encoder reranks it.

See docs/CROSS_ENCODER_DESIGN.md Section 5 for the architectural rationale
and Section 5b for the RRF formula and measured lift.
"""
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book
from backend.app.services.embedding import rank_by_embedding
from backend.app.services.gap_query_embedding import rank_by_gap_query_embedding
from backend.app.services.gap_scoring import rank_candidates
from backend.app.services.popularity import rank_by_popularity


# Source top-K defaults. Adjust here, not at call sites.
# See CROSS_ENCODER_DESIGN.md Section 5.2 for justification.
DEFAULT_TOP_K: dict[str, int] = {
    "gap": 50,
    "gap_query_embedding": 50,
    "embedding_read": 30,
    "popularity": 30,
}

# Source iteration order. Determines the merged pool's insertion order
# (used as a tiebreaker for recall@K when K < union pool size; primary
# ordering is handled by reciprocal_rank_fusion).
SOURCE_ORDER: list[str] = [
    "gap",
    "gap_query_embedding",
    "embedding_read",
    "popularity",
]


# Reciprocal Rank Fusion (RRF) weights and constant.
# Formula:  score(book) = Σ over sources of  weight_s / (RRF_K + rank_s(book))
# When source X did not surface a book, its contribution is 0 (skipped).
# k_constant = 60 (Cormack et al., TREC RRF paper).
#
# Weights v2.1 (4 sources, post-Phase-2 audit):
#   - gap: 1.0                 — user-specific, mission-aligned primary signal
#   - popularity: 0.7          — gap-blind broad coverage
#   - gap_query_embedding: 0.4 — long-tail discovery; deweighted from 1.2
#                                 after audit (see CROSS_ENCODER_DESIGN.md §5)
#   - embedding_read: 0.4      — mission-orthogonal; sharp when relevant
RRF_K_CONSTANT = 60
RRF_WEIGHTS: dict[str, float] = {
    "gap": 1.0,
    "popularity": 0.7,
    "gap_query_embedding": 0.4,
    "embedding_read": 0.4,
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
    gap_query_score: float | None = None
    gap_query_rank: int | None = None
    embedding_score: float | None = None
    embedding_rank: int | None = None
    popularity_rank: int | None = None


def generate_candidates(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k_overrides: dict[str, int] | None = None,
) -> list[Candidate]:
    """Run all Stage 1 retrievers and merge into one deduplicated pool.

    Order of insertion (gap → gap_query_embedding → embedding_read →
    popularity) determines tie-breaking when the pool is sliced at a
    K smaller than its size. RRF (reciprocal_rank_fusion) provides the
    real reordering.

    Returns list[Candidate] in insertion order.
    """
    top_k = {**DEFAULT_TOP_K, **(top_k_overrides or {})}
    read_set = set(read_book_ids)
    pool: dict[uuid.UUID, Candidate] = {}

    # ---- Source 1: gap_scoring ----
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

    # ---- Source 2: gap_query_embedding (NEW in Phase 2) ----
    gq_ranked = rank_by_gap_query_embedding(
        session, read_book_ids, top_k["gap_query_embedding"]
    )
    rank_in_source = 0
    for book, score in gq_ranked:
        if book.id in read_set:
            continue
        rank_in_source += 1
        candidate = pool.setdefault(book.id, Candidate(book_id=book.id))
        candidate.sources.append("gap_query_embedding")
        candidate.gap_query_score = float(score)
        candidate.gap_query_rank = rank_in_source

    # ---- Source 3: embedding_read ----
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

    # ---- Source 4: popularity ----
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

    For each candidate:
        score = Σ over sources of  weight_s / (k_constant + rank_in_source)

    Sources that didn't surface the candidate contribute 0.

    Returns a NEW list, sorted by RRF score descending.
    """
    w = weights or RRF_WEIGHTS

    rank_attr = {
        "gap": "gap_rank",
        "gap_query_embedding": "gap_query_rank",
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
