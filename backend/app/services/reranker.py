"""Cross-encoder reranking — Stage 2 of the retrieval pipeline.

The final, learned stage. Composes:

    read_book_ids
      -> generate_candidates()      Stage 1a: 4-source dedup pool (~110)
      -> reciprocal_rank_fusion()   Stage 1b: deterministic RRF order
      -> [cap to top CROSS_ENCODER_RERANK_K]   latency pre-filter
      -> CrossEncoder.predict()     Stage 2: learned (query, book) scores
      -> sorted by score desc

Returns list[tuple[Book, float]] — the same contract as gap_scoring,
popularity, tfidf, embedding. Score is the cross-encoder's sigmoid
output in [0, 1] (higher = more relevant).

Model: BAAI/bge-reranker-base fine-tuned on Atlas pairs (Phase 4).
Lives at models/cross_encoder_v1_epoch2 (gitignored). Loaded once as a
process-level singleton on first request.

Graceful degradation:
  - < 3 read books   -> popularity fallback (gap query too noisy, §3)
  - model unavailable -> RRF ordering fallback (loud warning, no 500)

See docs/CROSS_ENCODER_DESIGN.md §3 (query), §5 (pipeline), §9 (eval).
"""
import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book
from backend.app.services.candidate_generation import (
    generate_candidates,
    reciprocal_rank_fusion,
)
from backend.app.services.popularity import rank_by_popularity
from backend.app.services.query_builder import (
    build_candidate_text,
    build_user_query,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Configurable model path — defaults to the Phase 4 v1 checkpoint.
MODEL_PATH = Path(
    os.getenv(
        "ATLAS_CROSS_ENCODER_MODEL_PATH",
        str(REPO_ROOT / "models" / "cross_encoder_v1_epoch2"),
    )
)

# How many top-RRF candidates to actually rerank. The full pool (~110) is
# ~20-25s on CPU; capping trades a little recall for usable latency. Run a
# cap sweep (20/30/40/50/all) before documenting final quality.
CROSS_ENCODER_RERANK_K = int(os.getenv("ATLAS_CE_RERANK_K", "50"))

# Below this many read books, the gap vector is too noisy to build a
# meaningful query — fall back to popularity (§3 cold-start policy).
MIN_BOOKS_FOR_CROSS_ENCODER = 3

# Process-level singletons. _model_load_failed latches so we don't retry a
# missing/broken model on every request (would spam logs + waste time).
_model = None
_model_load_failed = False

def _get_model():
    """Lazy-load the cross-encoder once per process. Returns None if the
    model can't be loaded (missing checkout, corrupt files) — caller then
    falls back to RRF ordering."""
    global _model, _model_load_failed
    if _model is not None:
        return _model
    if _model_load_failed:
        return None
    if not MODEL_PATH.exists():
        logger.warning(
            "Cross-encoder model not found at %s — falling back to RRF "
            "ordering. Set ATLAS_CROSS_ENCODER_MODEL_PATH or train the "
            "model (scripts/train_cross_encoder.py).",
            MODEL_PATH,
        )
        _model_load_failed = True
        return None
    try:
        from sentence_transformers import CrossEncoder
        logger.info("Loading cross-encoder from %s ...", MODEL_PATH)
        _model = CrossEncoder(str(MODEL_PATH))
        logger.info("Cross-encoder loaded.")
        return _model
    except Exception:
        logger.exception(
            "Failed to load cross-encoder from %s — falling back to RRF "
            "ordering.", MODEL_PATH,
        )
        _model_load_failed = True
        return None

def _fetch_books(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, Book]:
    if not ids:
        return {}
    rows = session.execute(select(Book).where(Book.id.in_(ids))).scalars().all()
    return {b.id: b for b in rows}

def rank_by_cross_encoder(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k: int,
    recent_book_ids: list[uuid.UUID] | None = None,
) -> list[tuple[Book, float]]:
    """Rank candidate books with the full Stage 1 + Stage 2 pipeline.

    Args:
        read_book_ids: full reading history (drives gap vector + Stage 1).
        top_k: number of books to return.
        recent_book_ids: explicit recent-reading order for the query text
            (ordered by created_at DESC). If None, build_user_query falls
            back to read_book_ids[:2].

    Returns list[tuple[Book, float]] sorted by cross-encoder score desc.
    Falls back to popularity (<3 books) or RRF ordering (model missing).
    """
    # ---- Cold-start: gap query too noisy below the threshold (§3) ----
    if len(read_book_ids) < MIN_BOOKS_FOR_CROSS_ENCODER:
        return rank_by_popularity(session, read_book_ids, top_k)

    # ---- Stage 1a: candidate pool ----
    pool = generate_candidates(session, read_book_ids)
    if not pool:
        return []

    # ---- Stage 1b: RRF ordering (also the model-missing fallback) ----
    rrf_ordered = reciprocal_rank_fusion(pool)

    model = _get_model()
    books = _fetch_books(session, [c.book_id for c in rrf_ordered])

    if model is None:
        # Graceful degradation: return RRF order with placeholder scores.
        ranked = [
            (books[c.book_id], 0.0)
            for c in rrf_ordered
            if c.book_id in books
        ]
        return ranked[:top_k]

    # ---- Build query (single source of truth, §3) ----
    query = build_user_query(session, read_book_ids, recent_book_ids)
    if not query:
        # Defensive: empty query shouldn't happen above the cold-start
        # threshold, but fall back to RRF rather than feed the model junk.
        ranked = [
            (books[c.book_id], 0.0)
            for c in rrf_ordered
            if c.book_id in books
        ]
        return ranked[:top_k]

    # ---- Latency pre-filter: only rerank the top-K RRF candidates ----
    to_rerank = rrf_ordered[:CROSS_ENCODER_RERANK_K]

    pairs: list[tuple[str, str]] = []
    cand_books: list[Book] = []
    for c in to_rerank:
        book = books.get(c.book_id)
        if book is None:
            continue
        pairs.append((query, build_candidate_text(book)))
        cand_books.append(book)

    if not pairs:
        return []

    # ---- Stage 2: cross-encoder rerank ----
    scores = model.predict(pairs, show_progress_bar=False)
    ranked = sorted(
        zip(cand_books, (float(s) for s in scores)),
        key=lambda t: -t[1],
    )
    return ranked[:top_k]
