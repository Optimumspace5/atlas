"""Manual audit of training pairs (Phase 3.6, §6 mandatory quality gate).

Samples a stratified set of positives + hard negatives from the training
JSONL and prints them with full context for visual review.

Sample:
  20 positives
  10 hard_gap
  10 hard_embedding_read
  10 hard_popularity
  = 50 audit blocks total

Per §6 decision gate:
  > 3 mislabeled positives (of 20)             -> investigate positive rules
  > 4 mislabeled hard negatives (of 30 total)  -> investigate hard rules
  otherwise -> proceed to Phase 4 (training)

"weak" labels are tracked separately, NOT failures (informational only).

Random negatives are NOT audited per §6 (sampled outside archetype
affinity by construction, low false-negative risk).

Usage:
    python scripts/audit_training_pairs.py > audit_v1.txt
    python scripts/audit_training_pairs.py --seed 42

Then open audit_v1.txt in a text editor, walk through each block,
replace LABEL: ___ with one of {valid, weak, mislabeled}, and tally
at the bottom.

Requires DATABASE_URL in env.
"""
import argparse
import json
import os
import random
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402


DESCRIPTION_LIMIT = 300
DEFAULT_N_POSITIVES = 20
DEFAULT_N_HARD_PER_SOURCE = 10


def load_pairs(path: Path) -> list[dict]:
    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def fetch_annotations(
    session: Session, book_id: uuid.UUID
) -> list[tuple[str, float]]:
    rows = session.execute(
        select(Concept.slug, BookConceptAnnotation.strength)
        .join(BookConceptAnnotation, BookConceptAnnotation.concept_id == Concept.id)
        .where(BookConceptAnnotation.book_id == book_id)
        .order_by(BookConceptAnnotation.strength.desc())
    ).all()
    return [(slug, float(strength)) for slug, strength in rows]


def fetch_book(session: Session, book_id: uuid.UUID):
    return session.scalar(select(Book).where(Book.id == book_id))


def truncate(text: str | None, limit: int) -> str:
    t = (text or "").strip()
    if len(t) > limit:
        return t[:limit] + "..."
    return t


def stratified_sample(items: list, n: int, label: str, seed: int) -> list:
    """Deterministic per-label shuffle so adding categories later doesn't
    perturb earlier samples."""
    if not items:
        return []
    bucket = list(items)
    rng = random.Random(f"audit:{seed}:{label}")
    rng.shuffle(bucket)
    return bucket[:n]


def print_positive(session: Session, idx: int, n: int, pair: dict) -> None:
    print()
    print(f"[{idx}/{n}] POSITIVE")
    print(f"  user:        {pair['user_id']} ({pair['archetype']})")
    print(f"  split:       {pair['split']}")
    print(f"  query:       {pair['query']}")

    book_id = uuid.UUID(pair['candidate_book_id'])
    book = fetch_book(session, book_id)
    if book is None:
        print(f"  book:        (id {book_id}) -- NOT FOUND IN DB")
        return

    annotations = fetch_annotations(session, book_id)
    print(f"  book:        {book.title} -- {book.author}")
    if annotations:
        ann_str = ", ".join(f"{slug}({s:.1f})" for slug, s in annotations[:8])
        if len(annotations) > 8:
            ann_str += ", ..."
        print(f"  annotations: {ann_str}")
    desc = truncate(book.description, DESCRIPTION_LIMIT)
    if desc:
        print(f"  description: {desc}")
    print()
    print(f"  LABEL: ___________ (valid / weak / mislabeled)")


def print_hard_negative(session: Session, idx: int, n: int, pair: dict) -> None:
    print()
    print(f"[{idx}/{n}] {pair['negative_type'].upper()}")
    print(f"  user:        {pair['user_id']} ({pair['archetype']})")
    print(f"  split:       {pair['split']}")
    print(f"  query:       {pair['query']}")

    book_id = uuid.UUID(pair['candidate_book_id'])
    book = fetch_book(session, book_id)
    if book is None:
        print(f"  book:        (id {book_id}) -- NOT FOUND IN DB")
        return

    annotations = fetch_annotations(session, book_id)
    print(f"  book:        {book.title} -- {book.author}")
    if annotations:
        ann_str = ", ".join(f"{slug}({s:.1f})" for slug, s in annotations[:8])
        if len(annotations) > 8:
            ann_str += ", ..."
        print(f"  annotations: {ann_str}")
    else:
        print(f"  annotations: (none) -- ALERT: hard rule (c) requires annotated")
    print(f"  sources:     {pair.get('candidate_sources', [])}")
    score_parts = []
    if pair.get('gap_score') is not None:
        score_parts.append(f"gap_score={pair['gap_score']:.2f}")
    if pair.get('embedding_score') is not None:
        score_parts.append(f"embedding_score={pair['embedding_score']:.2f}")
    if pair.get('gap_query_score') is not None:
        score_parts.append(f"gap_query_score={pair['gap_query_score']:.2f}")
    if pair.get('popularity_rank') is not None:
        score_parts.append(f"popularity_rank={pair['popularity_rank']}")
    if score_parts:
        print(f"  scores:      {', '.join(score_parts)}")
    desc = truncate(book.description, DESCRIPTION_LIMIT)
    if desc:
        print(f"  description: {desc}")
    print()
    print(f"  LABEL: ___________ (valid / weak / mislabeled)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3.6 manual audit of cross-encoder training pairs."
    )
    parser.add_argument(
        "--jsonl", type=Path,
        default=REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl",
    )
    parser.add_argument("--n-positives", type=int, default=DEFAULT_N_POSITIVES)
    parser.add_argument(
        "--n-hard-per-source", type=int, default=DEFAULT_N_HARD_PER_SOURCE,
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    if not args.jsonl.exists():
        print(f"ERROR: training data not found at {args.jsonl}")
        print("Run scripts/generate_training_data.py first.")
        return 2

    pairs = load_pairs(args.jsonl)
    positives = [p for p in pairs if p['label'] == 1]
    hard_gap = [p for p in pairs if p.get('negative_type') == 'hard_gap']
    hard_emb = [p for p in pairs if p.get('negative_type') == 'hard_embedding_read']
    hard_pop = [p for p in pairs if p.get('negative_type') == 'hard_popularity']
    random_negs = [p for p in pairs if p.get('negative_type') == 'random']

    pos_sample = stratified_sample(positives, args.n_positives, "positive", args.seed)
    gap_sample = stratified_sample(hard_gap, args.n_hard_per_source, "hard_gap", args.seed)
    emb_sample = stratified_sample(hard_emb, args.n_hard_per_source, "hard_embedding", args.seed)
    pop_sample = stratified_sample(hard_pop, args.n_hard_per_source, "hard_popularity", args.seed)

    # ---- Header ----
    print("=" * 78)
    print("PHASE 3.6 MANUAL AUDIT -- Cross-encoder Training Pairs v1")
    print("=" * 78)
    print()
    print(f"Source:    {args.jsonl}")
    print(f"Seed:      {args.seed}")
    print()
    print("Dataset totals:")
    print(f"  positives:           {len(positives)}")
    print(f"  hard_gap:            {len(hard_gap)}")
    print(f"  hard_embedding_read: {len(hard_emb)}")
    print(f"  hard_popularity:     {len(hard_pop)}")
    print(f"  random:              {len(random_negs)}")
    print(f"  total:               {len(pairs)}")
    print()
    print("Audit sample:")
    print(f"  positives:           {len(pos_sample)}/{args.n_positives}")
    print(f"  hard_gap:            {len(gap_sample)}/{args.n_hard_per_source}")
    print(f"  hard_embedding_read: {len(emb_sample)}/{args.n_hard_per_source}")
    print(f"  hard_popularity:     {len(pop_sample)}/{args.n_hard_per_source}")
    print()
    print("INSTRUCTIONS:")
    print("  For each pair, replace LABEL: with one of:")
    print()
    print("  POSITIVES:")
    print("    valid       -- book genuinely fills a meaningful gap for this user")
    print("    weak        -- technically passes rule but marginal/dubious")
    print("    mislabeled  -- book should NOT be positive")
    print()
    print("  HARD NEGATIVES:")
    print("    valid       -- book is genuinely weak relative to user's gaps")
    print("    weak        -- somewhat weak but borderline")
    print("    mislabeled  -- book actually teaches a gap (FALSE NEGATIVE)")
    print()
    print("  DECISION GATE (§6):")
    print("    > 3 mislabeled positives (of 20)             -> investigate positive rules")
    print("    > 4 mislabeled hard negatives (of 30 total)  -> investigate hard rules")
    print("    weak labels are informational, not failures")
    print("    otherwise -> proceed to Phase 4 (training)")
    print()

    engine = create_engine(db_url)
    with Session(engine) as session:
        n_pos = len(pos_sample)
        print("=" * 78)
        print(f"POSITIVES ({n_pos})")
        print("=" * 78)
        for i, pair in enumerate(pos_sample, 1):
            print_positive(session, i, n_pos, pair)

        # Hard negatives grouped by source so you can think in batches
        # while walking through.
        all_hard = gap_sample + emb_sample + pop_sample
        n_hard = len(all_hard)
        print()
        print("=" * 78)
        print(f"HARD NEGATIVES ({n_hard})")
        print("=" * 78)
        for i, pair in enumerate(all_hard, 1):
            print_hard_negative(session, i, n_hard, pair)

    # ---- Footer (tally template) ----
    print()
    print()
    print("=" * 78)
    print("AUDIT COMPLETE -- TALLY YOUR LABELS")
    print("=" * 78)
    print()
    print("POSITIVES (of 20):")
    print("  valid:        ___")
    print("  weak:         ___")
    print("  mislabeled:   ___")
    print()
    print("HARD NEGATIVES by source (of 10 each):")
    print()
    print("  hard_gap:")
    print("    valid:       ___")
    print("    weak:        ___")
    print("    mislabeled:  ___")
    print()
    print("  hard_embedding_read:")
    print("    valid:       ___")
    print("    weak:        ___")
    print("    mislabeled:  ___")
    print()
    print("  hard_popularity:")
    print("    valid:       ___")
    print("    weak:        ___")
    print("    mislabeled:  ___")
    print()
    print("  HARD TOTAL (of 30):")
    print("    valid:       ___")
    print("    weak:        ___")
    print("    mislabeled:  ___")
    print()
    print("DECISION:")
    print("  positives mislabeled > 3   -> INVESTIGATE positive rules")
    print("  hard total mislabeled > 4  -> INVESTIGATE hard-negative rules")
    print("  otherwise -> proceed to Phase 4 (training)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
