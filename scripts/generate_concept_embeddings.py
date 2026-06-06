"""Generate sentence embeddings for every concept (parent + leaf).

Reads each concept's name + description, builds a rich document text,
encodes via BAAI/bge-small-en-v1.5 (same model as book_embeddings), and
stores 384-dim vectors in concept_embeddings.

These vectors power the gap_query_embedding service (Phase 2): at query
time, the user's top gap concept vectors are weighted-summed into a
single query vector, then book_embeddings are searched by cosine
distance to find books semantically related to the user's gaps —
bypassing the annotation-density constraint on direct gap scoring.

Idempotency:
    - Concepts already embedded for the CURRENT model are skipped.
    - Concepts embedded for a DIFFERENT model get re-embedded (PK is
      concept_id alone — only one embedding per concept).
    - --force re-embeds every concept regardless.

Usage:
    python scripts/generate_concept_embeddings.py
    python scripts/generate_concept_embeddings.py --force
    python scripts/generate_concept_embeddings.py --batch-size 16

Requires DATABASE_URL in env.
"""
import argparse
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Concept, ConceptEmbedding  # noqa: E402


MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_BATCH_SIZE = 16

# Format that goes into the bi-encoder. Rich enough that BGE can produce
# a meaningful semantic vector even for terse concept names. Matches the
# composition spec'd in CROSS_ENCODER_DESIGN.md Section 5.2.
DOCUMENT_TEMPLATE = "{name}. {description}"
FALLBACK_TEMPLATE = "{name}."  # when description is empty/null


def build_document(concept: Concept) -> str:
    """Compose embedding document text for one concept."""
    description = (concept.description or "").strip()
    if description:
        return DOCUMENT_TEMPLATE.format(
            name=concept.name, description=description
        )
    return FALLBACK_TEMPLATE.format(name=concept.name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate sentence embeddings for every concept."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every concept, even those that already have a row.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Encoder batch size (default {DEFAULT_BATCH_SIZE}).",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    # Lazy import: keeps --help snappy.
    print(f"Loading model {MODEL_NAME!r} (cached from book_embeddings run)...")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    if dim != 384:
        print(f"ERROR: model returned dim {dim}, but DB column is vector(384).")
        return 1
    print(f"Model loaded. Embedding dim: {dim}")

    engine = create_engine(db_url)
    with Session(engine) as session:
        all_concepts: list[Concept] = list(
            session.execute(select(Concept).order_by(Concept.slug)).scalars().all()
        )
        existing_ids: set = set(
            session.execute(
                select(ConceptEmbedding.concept_id).where(
                    ConceptEmbedding.model == MODEL_NAME
                )
            ).scalars().all()
        )

        if args.force:
            to_embed = all_concepts
        else:
            to_embed = [c for c in all_concepts if c.id not in existing_ids]

        print(f"Total concepts in DB:    {len(all_concepts)}")
        print(f"Already embedded (this model): {len(existing_ids)}")
        print(f"To embed this run:       {len(to_embed)}")

        if not to_embed:
            print("Nothing to do.")
            return 0

        docs = [build_document(c) for c in to_embed]
        print(f"\nEncoding {len(docs)} concepts (batch size {args.batch_size})...")
        start = time.monotonic()
        vectors = model.encode(
            docs,
            batch_size=args.batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        elapsed = time.monotonic() - start
        print(f"Encoded in {elapsed:.1f}s ({len(docs) / elapsed:.0f} concepts/s)")

        # Clear existing rows for these concepts (handles --force and
        # different-model cases — PK is concept_id alone).
        concept_ids_to_clear = [c.id for c in to_embed]
        deleted = session.execute(
            delete(ConceptEmbedding).where(
                ConceptEmbedding.concept_id.in_(concept_ids_to_clear)
            )
        )
        if deleted.rowcount:
            print(f"Cleared {deleted.rowcount} existing concept embedding row(s).")

        for concept, vector in zip(to_embed, vectors):
            session.add(
                ConceptEmbedding(
                    concept_id=concept.id,
                    embedding=vector.tolist(),
                    model=MODEL_NAME,
                )
            )
        session.commit()
        print(f"Inserted {len(to_embed)} concept embedding row(s).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
