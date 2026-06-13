"""Embedding-based ranking strategy.

For a user's reading history, compute the centroid (mean) of their book
embeddings, then rank candidate books by cosine similarity to that centroid.

Symmetry with other strategies — rank_candidates (gap), rank_by_popularity,
rank_by_tfidf — all return list[tuple[Book, float]] sorted descending by
score. Score here is cosine similarity (1 - cosine_distance), so:
    +1.0 = identical direction
     0.0 = orthogonal
    -1.0 = opposite

Requires BookEmbedding rows for the books in read_book_ids. Books without
embeddings contribute nothing to the centroid (silently skipped).
"""
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book, BookEmbedding


def rank_by_embedding(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k: int,
) -> list[tuple[Book, float]]:
    """Rank books by cosine similarity to the centroid of read books.

    Returns the top_k books most similar to the user, excluding already-read
    books. Returns [] if read_book_ids is empty or none of them have
    embeddings (no centroid to rank against).
    """
    if not read_book_ids:
        return []

    # Fetch embeddings for the user's read books.
    read_vectors = session.execute(
        select(BookEmbedding.embedding).where(
            BookEmbedding.book_id.in_(read_book_ids)
        )
    ).scalars().all()

    if not read_vectors:
        return []

    # Centroid = mean of read embeddings. The vectors were normalized at
    # write time, but the mean of unit vectors is NOT itself unit length —
    # we re-normalize so cosine_distance behaves consistently.
    centroid = np.mean(np.asarray(read_vectors), axis=0)
    norm = np.linalg.norm(centroid)
    if norm == 0:
        # Degenerate: all vectors cancelled out. Shouldn't happen with real
        # data, but guard against it (would otherwise produce NaN distances).
        return []
    centroid = centroid / norm

    # Query nearest neighbors via pgvector cosine distance (<=>).
    # `cosine_distance` returns a SQL expression we can use in ORDER BY.
    read_set = set(read_book_ids)
    distance_expr = BookEmbedding.embedding.cosine_distance(centroid.tolist())

    stmt = (
        select(Book, distance_expr.label("distance"))
        .join(BookEmbedding, BookEmbedding.book_id == Book.id)
        .where(Book.curated.is_(True), Book.id.notin_(read_set))
        .order_by(distance_expr)
        .limit(top_k)
    )
    rows = session.execute(stmt).all()

    # Convert distance to similarity so higher = better, matching the
    # contract of the other strategies.
    return [(book, 1.0 - float(dist)) for book, dist in rows]
