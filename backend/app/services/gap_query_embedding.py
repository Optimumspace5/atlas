"""Gap-query embedding ranker — uses the user's gap CONCEPTS as the query.

Atlas's mission is gap-fill. The bi-encoder strategy embedding_read uses
the user's READ books as the query, which is mission-orthogonal (returns
books similar to what the user already knows). This service flips that:
the query is the user's TOP GAP CONCEPTS, so retrieval is aligned with
"what should this user learn next."

Algorithm (Option B from CROSS_ENCODER_DESIGN.md Section 5):
    1. Compute user's gap vector from their reading history.
    2. Take top N gap concepts (gap > 0, sorted desc by gap value).
    3. Fetch their pre-computed embeddings from concept_embeddings.
    4. Compute weighted average (weight = gap value), then re-normalize.
    5. Cosine-search book_embeddings against this synthetic query vector.
    6. Filter already-read books; return top_k.

Why weighted average (not unweighted) and weighted by gap value (not by
priority rank): the biggest gap should pull the query vector hardest in
its direction. A user with gap_vector = {trend_analysis: 2.0,
support_resistance: 0.5} cares 4x more about trend_analysis; the query
vector should reflect that.

Why pre-computed concept embeddings (Option B), not on-the-fly text
encoding (Option A): no model loading at query time, just DB lookups.
~10ms vs ~500ms cold-start latency. Option A documented as future
experiment in CROSS_ENCODER_DESIGN.md.

Symmetric with the other ranking strategies: signature
    (session, read_book_ids, top_k) -> list[(Book, float)]
so candidate_generation.py can dispatch uniformly.
"""
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book, BookEmbedding, Concept, ConceptEmbedding
from backend.app.services.gap_scoring import get_gap_vector


# How many top gap concepts contribute to the query vector. Matches the
# top-5 used in CROSS_ENCODER_DESIGN.md Section 3 query format.
TOP_N_GAPS = 5


def rank_by_gap_query_embedding(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k: int,
) -> list[tuple[Book, float]]:
    """Rank books by cosine similarity to a synthetic gap-concept query vector.

    Returns the top_k books most similar to the user's "gap fingerprint,"
    excluding already-read books. Returns [] when:
        - read_book_ids is empty (no gap vector computable)
        - the user has zero gap (perfectly covered — unlikely)
        - none of the top gap concepts have embeddings
        - the weighted centroid degenerates to zero
    """
    if not read_book_ids:
        return []

    # 1. User's gap vector.
    gap_vector = get_gap_vector(session, read_book_ids)
    top_gaps = sorted(
        ((slug, gap) for slug, gap in gap_vector.items() if gap > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )[:TOP_N_GAPS]
    if not top_gaps:
        return []

    # 2. Fetch concept embeddings for those slugs.
    slugs = [slug for slug, _ in top_gaps]
    rows = session.execute(
        select(Concept.slug, ConceptEmbedding.embedding)
        .join(ConceptEmbedding, ConceptEmbedding.concept_id == Concept.id)
        .where(Concept.slug.in_(slugs))
    ).all()
    if not rows:
        return []

    # 3. Weighted average of concept embeddings (weight = gap value).
    gap_by_slug = dict(top_gaps)
    weighted_sum = np.zeros(384, dtype=np.float64)
    total_weight = 0.0
    for slug, embedding in rows:
        weight = gap_by_slug[slug]
        weighted_sum += np.asarray(embedding, dtype=np.float64) * weight
        total_weight += weight

    if total_weight == 0:
        return []

    query_vec = weighted_sum / total_weight

    # 4. Re-normalize. Embeddings were unit-normalized at write time, but
    #    a weighted average of unit vectors is not itself unit-length.
    #    Cosine distance assumes unit-length inputs.
    norm = np.linalg.norm(query_vec)
    if norm == 0:
        return []
    query_vec = query_vec / norm

    # 5. Search book_embeddings via pgvector cosine distance.
    read_set = set(read_book_ids)
    distance_expr = BookEmbedding.embedding.cosine_distance(query_vec.tolist())
    stmt = (
        select(Book, distance_expr.label("distance"))
        .join(BookEmbedding, BookEmbedding.book_id == Book.id)
        .where(Book.id.notin_(read_set))
        .order_by(distance_expr)
        .limit(top_k)
    )
    rows = session.execute(stmt).all()

    # Convert distance to similarity for consistency with the other
    # strategies' "higher = better" contract.
    return [(book, 1.0 - float(dist)) for book, dist in rows]
