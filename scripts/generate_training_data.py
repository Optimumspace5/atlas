"""Generate training data for the cross-encoder reranker.

PLANNED for Week 6. This is a stub — function bodies are TODO. The schema
and methodology are locked in docs/CROSS_ENCODER_DESIGN.md; this file is
the skeleton that Week 6 implementation will fill in.

Output: data/cross_encoder_pairs_v1.jsonl, one training pair per line.
Each line:

    {
      "user_id": str,
      "archetype": str,
      "query": str,                      # hybrid: gaps + recent books
      "candidate_book_id": str (UUID),
      "candidate_text": str,             # title + author + description
      "label": 0 | 1,
      "negative_type": null | "random" | "hard",
      "split": "train" | "val" | "test"
    }

Usage (when implemented):
    python scripts/generate_training_data.py
    python scripts/generate_training_data.py --n-users 50 --seed 42

Requires DATABASE_URL in env.
"""
import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book  # noqa: E402


# -----------------------------------------------------------------------------
# Configuration (mirrors docs/CROSS_ENCODER_DESIGN.md)
# -----------------------------------------------------------------------------
DEFAULT_N_USERS = 25
DEFAULT_HELDOUT_PER_USER = 3
DEFAULT_NEGATIVES_PER_POSITIVE = 4         # 2 random + 2 hard
DEFAULT_HARD_FRACTION = 0.5
DEFAULT_TOP_GAPS_IN_QUERY = 5
DEFAULT_ANCHOR_BOOKS_IN_QUERY = 2
DEFAULT_BIENCODER_TOP_K = 50               # candidate window for hard negs
DEFAULT_HARD_NEG_RANGE = (5, 50)           # avoid ranks 0-4 (likely near-positive)

# Split fractions (must sum to 1.0).
SPLIT_FRACTIONS = {"train": 0.8, "val": 0.1, "test": 0.1}

OUTPUT_PATH = REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl"


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------
@dataclass
class SyntheticUser:
    """One synthetic user from the archetype generator (eval harness)."""
    user_id: str
    archetype: str
    read_book_ids: list[uuid.UUID]


@dataclass
class TrainingPair:
    """One row of the output JSONL."""
    user_id: str
    archetype: str
    query: str
    candidate_book_id: uuid.UUID
    candidate_text: str
    label: int                      # 0 or 1
    negative_type: str | None       # None for positives; "random" | "hard" for negatives
    split: str                      # "train" | "val" | "test"


# -----------------------------------------------------------------------------
# Stage functions (TODO bodies — Week 6)
# -----------------------------------------------------------------------------
def generate_synthetic_users(session: Session, n_users: int, seed: int) -> list[SyntheticUser]:
    """Build N synthetic archetype users.

    TODO: Reuse the archetype generator from scripts/evaluate_baselines.py.
    Factor it into a shared helper or import directly. Each archetype
    produces n_users // n_archetypes users, weighted by concept affinity.

    Output: list of SyntheticUser, each with 8-12 read_book_ids.
    """
    raise NotImplementedError("Week 6")


def hold_out(user: SyntheticUser, k: int, seed: int) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Split a user's reading into (kept, held_out).

    TODO: Random sample k books from user.read_book_ids; return (rest, held_out).
    Deterministic given (user.user_id, seed) so the same split is reproducible.
    """
    raise NotImplementedError("Week 6")


def build_query(session: Session, user_id: str, kept_book_ids: list[uuid.UUID]) -> str:
    """Format the hybrid query string for one user.

    Format (locked in CROSS_ENCODER_DESIGN.md):
        "Reader gaps: {top_5_gap_slugs}. Recent reading: {title} by {author}; ..."

    TODO:
        1. Call get_gap_vector(session, kept_book_ids) from services/gap_scoring.py
        2. Take top DEFAULT_TOP_GAPS_IN_QUERY slugs by gap value (filter gap > 0)
        3. Fetch DEFAULT_ANCHOR_BOOKS_IN_QUERY most recent books from kept_book_ids
           (need created_at — query user_books with these book_ids, order desc)
        4. Format the f-string and return
    """
    raise NotImplementedError("Week 6")


def build_candidate_text(book: Book) -> str:
    """Format a book as the candidate text shown to the reranker.

    Matches the document composition used by services/tfidf.py and
    scripts/generate_embeddings.py.
    """
    parts = [book.title, book.author]
    if book.description:
        parts.append(book.description)
    return " ".join(parts)


def sample_random_negatives(
    session: Session,
    user: SyntheticUser,
    n: int,
    exclude_book_ids: set[uuid.UUID],
    seed: int,
) -> list[uuid.UUID]:
    """Sample n random books outside the user's reading and held-out set.

    TODO: SELECT id FROM books WHERE id NOT IN exclude AND id NOT IN read.
    Also exclude books strongly annotated against the user's archetype
    concepts (to make these true "easy" negatives — far from anything the
    user would want).
    """
    raise NotImplementedError("Week 6")


def sample_hard_negatives(
    session: Session,
    user: SyntheticUser,
    held_out_book_id: uuid.UUID,
    kept_book_ids: list[uuid.UUID],
    n: int,
    seed: int,
) -> list[uuid.UUID]:
    """Sample n bi-encoder near-misses as hard negatives for one positive.

    TODO:
        1. Run stage-1 BGE retrieval: rank_by_embedding(session, kept_book_ids, top_k=DEFAULT_BIENCODER_TOP_K)
        2. Drop the held-out book itself if present in the result
        3. Sample n book_ids from positions DEFAULT_HARD_NEG_RANGE
    """
    raise NotImplementedError("Week 6")


def assign_split(user_id: str) -> str:
    """Hash-partition users into train/val/test by user_id.

    Same user_id always maps to the same split, so a user never appears in
    both train and val (prevents memorisation of user-specific patterns).
    """
    h = int(hashlib.sha256(user_id.encode()).hexdigest(), 16) / 2**256
    cumulative = 0.0
    for split, frac in SPLIT_FRACTIONS.items():
        cumulative += frac
        if h < cumulative:
            return split
    return "test"  # rounding safety


# -----------------------------------------------------------------------------
# Driver (TODO body — Week 6)
# -----------------------------------------------------------------------------
def generate_pairs(
    session: Session,
    n_users: int,
    heldout_per_user: int,
    negatives_per_positive: int,
    seed: int,
) -> list[TrainingPair]:
    """Top-level pair generator.

    TODO (Week 6):
        users = generate_synthetic_users(session, n_users, seed)
        pairs = []
        for user in users:
            kept, held_out = hold_out(user, heldout_per_user, seed)
            query = build_query(session, user.user_id, kept)
            split = assign_split(user.user_id)
            for positive_book_id in held_out:
                # Positive pair
                pairs.append(TrainingPair(
                    user_id=user.user_id,
                    archetype=user.archetype,
                    query=query,
                    candidate_book_id=positive_book_id,
                    candidate_text=build_candidate_text(<fetch Book>),
                    label=1,
                    negative_type=None,
                    split=split,
                ))
                # Negatives — half random, half hard
                n_random = negatives_per_positive // 2
                n_hard = negatives_per_positive - n_random
                random_negs = sample_random_negatives(...)
                hard_negs = sample_hard_negatives(...)
                for neg_id, neg_type in (
                    [(b, "random") for b in random_negs] +
                    [(b, "hard") for b in hard_negs]
                ):
                    pairs.append(TrainingPair(..., label=0, negative_type=neg_type, split=split))
        return pairs
    """
    raise NotImplementedError("Week 6")


def write_jsonl(pairs: list[TrainingPair], path: Path) -> None:
    """Atomic write: serialise pairs to a temp file, then os.replace."""
    tmp_path = path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps({
                "user_id": pair.user_id,
                "archetype": pair.archetype,
                "query": pair.query,
                "candidate_book_id": str(pair.candidate_book_id),
                "candidate_text": pair.candidate_text,
                "label": pair.label,
                "negative_type": pair.negative_type,
                "split": pair.split,
            }, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


# -----------------------------------------------------------------------------
# CLI entry
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate cross-encoder training pairs (Week 6 stub)."
    )
    parser.add_argument("--n-users", type=int, default=DEFAULT_N_USERS)
    parser.add_argument("--heldout-per-user", type=int, default=DEFAULT_HELDOUT_PER_USER)
    parser.add_argument(
        "--negatives-per-positive", type=int, default=DEFAULT_NEGATIVES_PER_POSITIVE
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_PATH,
        help="Output JSONL path.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    print("This script is a STUB. Body is planned for Week 6.")
    print("See docs/CROSS_ENCODER_DESIGN.md for the locked-in schema.")
    print()
    print(f"Configured to generate ~{args.n_users * args.heldout_per_user * (1 + args.negatives_per_positive)} pairs")
    print(f"  ({args.n_users} users × {args.heldout_per_user} held-out × {1 + args.negatives_per_positive} pairs per held-out)")
    print(f"Output target: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
