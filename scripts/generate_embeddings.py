"""Generate sentence embeddings for every book in the catalog.

Loads books, builds documents (title + author + description), encodes via
BAAI/bge-small-en-v1.5, and stores 384-dim vectors in book_embeddings.

After insertion, creates the IVFFlat index on the embedding column. The
index is built AFTER population so centroids cluster on real vectors.

Idempotency:
    - Books that already have an embedding for the CURRENT model are skipped.
    - Books with an embedding for a DIFFERENT model get their old row
      deleted before the new vector is inserted (PK is book_id alone).
    - --force re-embeds every book regardless.

Usage:
    python scripts/generate_embeddings.py
    python scripts/generate_embeddings.py --force
    python scripts/generate_embeddings.py --batch-size 16

Requires DATABASE_URL in env.
"""
import argparse
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookEmbedding  # noqa: E402


MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_BATCH_SIZE = 32

# IVFFlat index parameter. Rule of thumb: lists ~ sqrt(N).
# Bump when corpus grows (1k -> 32, 10k -> 100, 100k -> 316).
IVFFLAT_LISTS = 22


def build_document(book: Book) -> str:
    """Compose the embedding document text for one book.

    Matches the document composition used by services/tfidf.py so the two
    strategies can be compared apples-to-apples.
    """
    parts = [book.title, book.author]
    if book.description:
        parts.append(book.description)
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate sentence embeddings for every book."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every book, even those that already have a current-model row.",
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
        print("ERROR: DATABASE_URL not set. Example:")
        print('  $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"')
        return 2

    # Lazy import: keeps `--help` snappy and avoids loading torch unless we
    # actually need it.
    print(f"Loading model {MODEL_NAME!r} (first run downloads ~130 MB)...")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    if dim != 384:
        print(f"ERROR: model returned dim {dim}, but DB column is vector(384).")
        return 1
    print(f"Model loaded. Embedding dim: {dim}")

    engine = create_engine(db_url)
    with Session(engine) as session:
        all_books: list[Book] = list(
            session.execute(select(Book).order_by(Book.id)).scalars().all()
        )
        existing_ids: set = set(
            session.execute(
                select(BookEmbedding.book_id).where(
                    BookEmbedding.model == MODEL_NAME
                )
            ).scalars().all()
        )

        if args.force:
            to_embed = all_books
        else:
            to_embed = [b for b in all_books if b.id not in existing_ids]

        print(f"Total books in DB:        {len(all_books)}")
        print(f"Already embedded (current model): {len(existing_ids)}")
        print(f"To embed this run:        {len(to_embed)}")

        if not to_embed:
            print("Nothing to do.")
            _ensure_index(session)
            return 0

        docs = [build_document(b) for b in to_embed]
        print(f"\nEncoding {len(docs)} documents (batch size {args.batch_size})...")
        start = time.monotonic()
        vectors = model.encode(
            docs,
            batch_size=args.batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        elapsed = time.monotonic() - start
        print(f"Encoded in {elapsed:.1f}s ({len(docs) / elapsed:.0f} docs/s)")

        # Clear any existing rows for these books (handles --force and the
        # "different model name" case). PK is book_id alone, so duplicates
        # would otherwise hit a PK violation.
        book_ids_to_clear = [b.id for b in to_embed]
        deleted = session.execute(
            delete(BookEmbedding).where(BookEmbedding.book_id.in_(book_ids_to_clear))
        )
        if deleted.rowcount:
            print(f"Cleared {deleted.rowcount} existing embedding row(s).")

        for book, vector in zip(to_embed, vectors):
            session.add(
                BookEmbedding(
                    book_id=book.id,
                    embedding=vector.tolist(),
                    model=MODEL_NAME,
                )
            )
        session.commit()
        print(f"Inserted {len(to_embed)} embedding row(s).")

        _ensure_index(session)

    return 0


def _ensure_index(session: Session) -> None:
    """Create the IVFFlat cosine-similarity index if it doesn't exist.

    pgvector needs the table to contain vectors when the index is built so
    centroids cluster on real data. Running this at the end of the script,
    after the INSERTs, satisfies that.
    """
    print(f"\nEnsuring IVFFlat index (lists={IVFFLAT_LISTS})...")
    session.execute(
        text(f"""
            CREATE INDEX IF NOT EXISTS book_embeddings_ivfflat_idx
            ON book_embeddings
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = {IVFFLAT_LISTS})
        """)
    )
    session.commit()
    print("Index ready.")


if __name__ == "__main__":
    sys.exit(main())
