"""Generate training data for the cross-encoder reranker.

Schema is locked in docs/CROSS_ENCODER_DESIGN.md §6 (labeling) and §7
(output format). Phase 3.5 calibration: rule (d) reformulated from
"alphabetical top-3 gap concepts" to "any concept where user has
meaningful gap" — fixes the gap-tie alphabetical-ordering bottleneck
observed in Phase 3.4 (178/273 held-outs unfairly rejected).

Output: data/cross_encoder_pairs_v1.jsonl, one training pair per line.

Usage:
    python scripts/generate_training_data.py
    python scripts/generate_training_data.py --n-users 100 --seed 42
    python scripts/generate_training_data.py --dry-run

Requires DATABASE_URL in env.
"""
import argparse
import hashlib
import json
import os
import random
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402
from backend.app.services.candidate_generation import (  # noqa: E402
    Candidate,
    generate_candidates,
)
from backend.app.services.gap_scoring import get_gap_vector  # noqa: E402
from backend.app.services.query_builder import (  # noqa: E402
    build_candidate_text,
    build_user_query,
)
from scripts.evaluate_baselines import (  # noqa: E402
    ARCHETYPES,
    generate_synthetic_user,
    load_archetype_weights,
)


# -----------------------------------------------------------------------------
# Configuration (mirrors docs/CROSS_ENCODER_DESIGN.md §6, Phase 3.5 calibrated)
# -----------------------------------------------------------------------------
DEFAULT_N_USERS = 100
DEFAULT_HELDOUT_PER_USER = 3
DEFAULT_RANDOM_NEGATIVES_PER_POSITIVE = 1
DEFAULT_HARD_NEGATIVES_PER_SOURCE = 2

HARD_NEGATIVE_SOURCES = ["gap", "embedding_read", "popularity"]

# §6 rule thresholds — calibrated in Phase 3.5.
# Original §6 design referenced "top-3 user gap concepts" for rules (d) on
# both positive and hard-negative classification. Phase 3.4 measured 178/273
# in-pool held-outs failing rule (d) because the alphabetical tie-break on
# saturated gaps (~18 concepts tied at gap=2.0 per user) made "top-3" arbitrary.
# Reformulated to "any concept where gap >= MEANINGFUL_GAP_THRESHOLD" — same
# label quality, no alphabetical artifact.
MEANINGFUL_GAP_THRESHOLD = 1.0          # user must have gap >= this to count as a meaningful gap
MIN_STRONG_CONCEPT_STRENGTH = 0.5       # book annotation strength must be >= this to count as "strongly teaches"
HARD_NEGATIVE_GAP_MARGIN = 0.3          # hard rule (e)
ARCHETYPE_AFFINITY_MIN_STRENGTH = 0.5   # random-negative exclusion threshold

SPLIT_FRACTIONS = {"train": 0.8, "val": 0.1, "test": 0.1}
OUTPUT_PATH = REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl"


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------
@dataclass
class SyntheticUser:
    user_id: str
    archetype: str
    read_book_ids: list[uuid.UUID]


@dataclass
class TrainingPair:
    """Locked §7 schema."""
    user_id: str
    archetype: str
    query: str
    candidate_book_id: uuid.UUID
    candidate_text: str
    label: int
    negative_type: str | None
    candidate_sources: list[str] = field(default_factory=list)
    gap_score: float | None = None
    gap_query_score: float | None = None
    embedding_score: float | None = None
    popularity_rank: int | None = None
    is_annotated: bool = False
    split: str = ""


@dataclass
class GenerationStats:
    """Diagnostic counters surfaced at end-of-run."""
    users_processed: int = 0
    pools_generated: int = 0
    mean_pool_size: float = 0.0
    heldouts_total: int = 0
    heldouts_in_pool: int = 0
    positives_emitted: int = 0
    ambiguous_skipped_heldouts: int = 0
    positive_rule_failures: dict[str, int] = field(default_factory=lambda: {
        "no_annotation": 0,
        "no_meaningful_gap": 0,
        "no_meaningful_gap_strength": 0,
    })
    hard_qualified_by_source: dict[str, int] = field(default_factory=dict)
    hard_emitted_by_source: dict[str, int] = field(default_factory=dict)
    random_negatives_emitted: int = 0


# -----------------------------------------------------------------------------
# Module-level caches
# -----------------------------------------------------------------------------
_AFFINITY_CACHE: dict[str, set[uuid.UUID]] = {}
_ALL_BOOKS_CACHE: list[uuid.UUID] | None = None


def _all_book_ids(session: Session) -> list[uuid.UUID]:
    global _ALL_BOOKS_CACHE
    if _ALL_BOOKS_CACHE is None:
        _ALL_BOOKS_CACHE = list(
            session.execute(select(Book.id)).scalars().all()
        )
    return _ALL_BOOKS_CACHE


def _archetype_affinity_book_ids(
    session: Session, archetype_name: str
) -> set[uuid.UUID]:
    """Books annotated >= ARCHETYPE_AFFINITY_MIN_STRENGTH on any home or
    secondary concept of the archetype. EXCLUDED from random negatives
    (random negs should be unambiguously off-topic).
    """
    if archetype_name in _AFFINITY_CACHE:
        return _AFFINITY_CACHE[archetype_name]
    spec = ARCHETYPES.get(archetype_name)
    if spec is None:
        _AFFINITY_CACHE[archetype_name] = set()
        return set()

    home_parent_id = session.scalar(
        select(Concept.id).where(Concept.slug == spec["home"])
    )
    home_leaf_ids = (
        list(session.execute(
            select(Concept.id).where(Concept.parent_id == home_parent_id)
        ).scalars().all())
        if home_parent_id is not None
        else []
    )
    secondary_leaf_ids = list(session.execute(
        select(Concept.id).where(Concept.slug.in_(spec["secondary"]))
    ).scalars().all())
    affinity_concept_ids = home_leaf_ids + secondary_leaf_ids

    if not affinity_concept_ids:
        _AFFINITY_CACHE[archetype_name] = set()
        return set()

    result = set(session.execute(
        select(BookConceptAnnotation.book_id)
        .where(BookConceptAnnotation.concept_id.in_(affinity_concept_ids))
        .where(BookConceptAnnotation.strength >= ARCHETYPE_AFFINITY_MIN_STRENGTH)
    ).scalars().all())
    _AFFINITY_CACHE[archetype_name] = result
    return result


# -----------------------------------------------------------------------------
# Step 1: synthetic users
# -----------------------------------------------------------------------------
def generate_synthetic_users(
    session: Session, n_users: int, seed: int
) -> list[SyntheticUser]:
    """Build up to n_users synthetic archetype users, distributed evenly."""
    rng = random.Random(seed)
    per_archetype = max(1, n_users // len(ARCHETYPES))
    users: list[SyntheticUser] = []

    for archetype_name, spec in ARCHETYPES.items():
        home_w, sec_w = load_archetype_weights(session, spec)
        for i in range(per_archetype):
            result = generate_synthetic_user(home_w, sec_w, rng)
            if result is None:
                continue
            kept_ids, heldout_ids = result
            users.append(SyntheticUser(
                user_id=f"synthetic-{archetype_name}-u{i}",
                archetype=archetype_name,
                read_book_ids=list(kept_ids) + list(heldout_ids),
            ))

    return users


# -----------------------------------------------------------------------------
# Step 2: hold-out
# -----------------------------------------------------------------------------
def hold_out(
    user: SyntheticUser, k: int, seed: int
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    h = int(hashlib.sha256(f"{user.user_id}:{seed}".encode()).hexdigest(), 16)
    rng = random.Random(h)
    shuffled = list(user.read_book_ids)
    rng.shuffle(shuffled)
    if k >= len(shuffled):
        k = max(0, len(shuffled) - 1)
    held_out = shuffled[:k]
    kept = sorted(shuffled[k:], key=str)
    return kept, held_out


# -----------------------------------------------------------------------------
# Step 3: batch annotation fetch
# -----------------------------------------------------------------------------
def batch_fetch_annotations(
    session: Session, book_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[str, float]]]:
    if not book_ids:
        return {}
    rows = session.execute(
        select(
            BookConceptAnnotation.book_id,
            Concept.slug,
            BookConceptAnnotation.strength,
        )
        .join(Concept, BookConceptAnnotation.concept_id == Concept.id)
        .where(BookConceptAnnotation.book_id.in_(book_ids))
    ).all()
    result: dict[uuid.UUID, list[tuple[str, float]]] = defaultdict(list)
    for book_id, slug, strength in rows:
        result[book_id].append((slug, float(strength)))
    return dict(result)


# -----------------------------------------------------------------------------
# Step 4: labelers (Phase 3.5 — gap-meaningful concept rules)
# -----------------------------------------------------------------------------
def classify_held_out(
    candidate: Candidate,
    pool_annotations: dict[uuid.UUID, list[tuple[str, float]]],
    gap_vector: dict[str, float],
) -> str:
    """Apply §6 positive rules (b-d), Phase 3.5 reformulation. Returns:
        "positive"                    — qualified positive (label=1)
        "no_annotation"               — rule (b) failed
        "no_meaningful_gap"           — book has no annotation on a concept
                                         where the user has gap >= MEANINGFUL_GAP_THRESHOLD
        "no_meaningful_gap_strength"  — book annotated on gap-meaningful concepts
                                         but no strength >= MIN_STRONG_CONCEPT_STRENGTH
    """
    annotations = pool_annotations.get(candidate.book_id, [])
    if not annotations:
        return "no_annotation"
    gap_meaningful = [
        (slug, strength) for slug, strength in annotations
        if gap_vector.get(slug, 0.0) >= MEANINGFUL_GAP_THRESHOLD
    ]
    if not gap_meaningful:
        return "no_meaningful_gap"
    if not any(strength >= MIN_STRONG_CONCEPT_STRENGTH
               for _, strength in gap_meaningful):
        return "no_meaningful_gap_strength"
    return "positive"


def sample_hard_X(
    pool: list[Candidate],
    source: str,
    positive: Candidate,
    pool_annotations: dict[uuid.UUID, list[tuple[str, float]]],
    gap_vector: dict[str, float],
    held_out_ids: set[uuid.UUID],
) -> list[Candidate]:
    """Apply §6 hard-negative rules (a-e), Phase 3.5 reformulation of (d):
        (d') candidate has NO strength >= MIN_STRONG_CONCEPT_STRENGTH on any
             concept where user has gap >= MEANINGFUL_GAP_THRESHOLD.

    Returns ALL qualified candidates sorted by source rank ASC
    (most-confusing first). Caller picks the top-N to emit.

    On rule (e) and missing gap_score: a candidate not surfaced by gap
    (gap_score=None) is treated as gap_score=0.0 — it sits below the gap
    top-50 floor, which is conservatively weaker than any positive.
    """
    pos_gap = positive.gap_score or 0.0
    rank_attr = {
        "gap": "gap_rank",
        "embedding_read": "embedding_rank",
        "popularity": "popularity_rank",
    }[source]

    qualified: list[Candidate] = []
    for cand in pool:
        if source not in cand.sources:
            continue
        if cand.book_id in held_out_ids:
            continue
        annotations = pool_annotations.get(cand.book_id, [])
        if not annotations:
            continue
        # (d') no strong annotation on any gap-meaningful concept
        if any(
            strength >= MIN_STRONG_CONCEPT_STRENGTH
            and gap_vector.get(slug, 0.0) >= MEANINGFUL_GAP_THRESHOLD
            for slug, strength in annotations
        ):
            continue
        # (e) gap_score margin
        cand_gap = cand.gap_score or 0.0
        if cand_gap >= pos_gap - HARD_NEGATIVE_GAP_MARGIN:
            continue
        qualified.append(cand)

    qualified.sort(key=lambda c: getattr(c, rank_attr) or 999_999)
    return qualified


def sample_random_negatives(
    session: Session,
    user: SyntheticUser,
    n: int,
    exclude_book_ids: set[uuid.UUID],
    seed: int,
) -> list[uuid.UUID]:
    if n <= 0:
        return []
    affinity = _archetype_affinity_book_ids(session, user.archetype)
    all_books = _all_book_ids(session)
    eligible = [
        bid for bid in all_books
        if bid not in exclude_book_ids and bid not in affinity
    ]
    if not eligible:
        return []
    rng = random.Random(f"random:{user.user_id}:{seed}")
    rng.shuffle(eligible)
    return eligible[:n]


# -----------------------------------------------------------------------------
# Step 5: split + helpers
# -----------------------------------------------------------------------------
def assign_split(user_id: str) -> str:
    h = int(hashlib.sha256(user_id.encode()).hexdigest(), 16) / 2**256
    cumulative = 0.0
    for split, frac in SPLIT_FRACTIONS.items():
        cumulative += frac
        if h < cumulative:
            return split
    return "test"


def _make_pair(
    user: SyntheticUser,
    query: str,
    candidate: Candidate,
    candidate_text: str,
    label: int,
    negative_type: str | None,
    is_annotated: bool,
    split: str,
) -> TrainingPair:
    return TrainingPair(
        user_id=user.user_id,
        archetype=user.archetype,
        query=query,
        candidate_book_id=candidate.book_id,
        candidate_text=candidate_text,
        label=label,
        negative_type=negative_type,
        candidate_sources=list(candidate.sources),
        gap_score=candidate.gap_score,
        gap_query_score=candidate.gap_query_score,
        embedding_score=candidate.embedding_score,
        popularity_rank=candidate.popularity_rank,
        is_annotated=is_annotated,
        split=split,
    )


# -----------------------------------------------------------------------------
# Step 6: driver
# -----------------------------------------------------------------------------
def generate_pairs(
    session: Session,
    n_users: int,
    heldout_per_user: int,
    random_negatives_per_positive: int,
    hard_negatives_per_source: int,
    seed: int,
) -> tuple[list[TrainingPair], GenerationStats]:
    """Walk Stage 1 pools per §6 and emit positive + hard + random pairs."""
    stats = GenerationStats(
        hard_qualified_by_source={s: 0 for s in HARD_NEGATIVE_SOURCES},
        hard_emitted_by_source={s: 0 for s in HARD_NEGATIVE_SOURCES},
    )
    pairs: list[TrainingPair] = []
    pool_size_total = 0

    users = generate_synthetic_users(session, n_users, seed)

    for user in users:
        stats.users_processed += 1
        kept, held_out_ids = hold_out(user, heldout_per_user, seed)
        if not kept:
            continue

        split = assign_split(user.user_id)
        query = build_user_query(session, kept)
        if not query:
            continue

        pool = generate_candidates(session, kept)
        stats.pools_generated += 1
        pool_size_total += len(pool)
        pool_by_id: dict[uuid.UUID, Candidate] = {c.book_id: c for c in pool}

        pool_ids = list(pool_by_id.keys())
        annotations = batch_fetch_annotations(session, pool_ids)
        books_by_id = {
            b.id: b for b in session.execute(
                select(Book).where(Book.id.in_(pool_ids))
            ).scalars().all()
        }

        gap_vector = get_gap_vector(session, kept)
        held_out_set = set(held_out_ids)

        # ---- Classify each held-out per §6 positive rules ----
        positives: list[Candidate] = []
        for heldout_id in held_out_ids:
            stats.heldouts_total += 1
            cand = pool_by_id.get(heldout_id)
            if cand is None:
                continue
            stats.heldouts_in_pool += 1
            verdict = classify_held_out(cand, annotations, gap_vector)
            if verdict == "positive":
                positives.append(cand)
            else:
                stats.positive_rule_failures[verdict] += 1
                stats.ambiguous_skipped_heldouts += 1

        # ---- Per-positive: emit positive + hard negs + random negs ----
        for pos in positives:
            pos_book = books_by_id.get(pos.book_id)
            if pos_book is None:
                continue
            pairs.append(_make_pair(
                user=user,
                query=query,
                candidate=pos,
                candidate_text=build_candidate_text(pos_book),
                label=1,
                negative_type=None,
                is_annotated=bool(annotations.get(pos.book_id)),
                split=split,
            ))
            stats.positives_emitted += 1

            for source in HARD_NEGATIVE_SOURCES:
                qualified = sample_hard_X(
                    pool=pool,
                    source=source,
                    positive=pos,
                    pool_annotations=annotations,
                    gap_vector=gap_vector,
                    held_out_ids=held_out_set,
                )
                stats.hard_qualified_by_source[source] += len(qualified)
                for h in qualified[:hard_negatives_per_source]:
                    h_book = books_by_id.get(h.book_id)
                    if h_book is None:
                        continue
                    pairs.append(_make_pair(
                        user=user,
                        query=query,
                        candidate=h,
                        candidate_text=build_candidate_text(h_book),
                        label=0,
                        negative_type=f"hard_{source}",
                        is_annotated=bool(annotations.get(h.book_id)),
                        split=split,
                    ))
                    stats.hard_emitted_by_source[source] += 1

            exclude = (
                set(pool_by_id.keys())
                | set(user.read_book_ids)
                | held_out_set
            )
            random_ids = sample_random_negatives(
                session=session,
                user=user,
                n=random_negatives_per_positive,
                exclude_book_ids=exclude,
                seed=seed,
            )
            if random_ids:
                rbooks = {
                    b.id: b for b in session.execute(
                        select(Book).where(Book.id.in_(random_ids))
                    ).scalars().all()
                }
                rannotations = batch_fetch_annotations(session, random_ids)
                for bid in random_ids:
                    book = rbooks.get(bid)
                    if book is None:
                        continue
                    fake_cand = Candidate(book_id=bid)
                    pairs.append(_make_pair(
                        user=user,
                        query=query,
                        candidate=fake_cand,
                        candidate_text=build_candidate_text(book),
                        label=0,
                        negative_type="random",
                        is_annotated=bool(rannotations.get(bid)),
                        split=split,
                    ))
                    stats.random_negatives_emitted += 1

    stats.mean_pool_size = (
        pool_size_total / stats.pools_generated if stats.pools_generated else 0.0
    )
    return pairs, stats


def print_stats(stats: GenerationStats) -> None:
    print("\nPhase 3.5 run:")
    print(f"  users processed:              {stats.users_processed}")
    print(f"  candidate pools generated:    {stats.pools_generated}")
    print(f"  mean pool size:               {stats.mean_pool_size:.1f}")
    print()
    print("  HELD-OUT CLASSIFICATION:")
    print(f"    held-outs total:            {stats.heldouts_total}")
    print(f"    held-outs found in pool:    {stats.heldouts_in_pool}")
    print(f"    -> positive:                {stats.positives_emitted}")
    print(f"    -> no_annotation:           {stats.positive_rule_failures['no_annotation']}")
    print(f"    -> no_meaningful_gap:       {stats.positive_rule_failures['no_meaningful_gap']}")
    print(f"    -> no_meaningful_gap_strength: {stats.positive_rule_failures['no_meaningful_gap_strength']}")
    print(f"    ambiguous_skipped total:    {stats.ambiguous_skipped_heldouts}")
    print()
    print("  HARD NEGATIVES (per source):")
    print(f"    {'source':<18} {'qualified':>10}  {'emitted':>8}")
    for s in HARD_NEGATIVE_SOURCES:
        q = stats.hard_qualified_by_source.get(s, 0)
        e = stats.hard_emitted_by_source.get(s, 0)
        print(f"    {s:<18} {q:>10}  {e:>8}")
    print()
    print(f"  RANDOM negatives emitted:     {stats.random_negatives_emitted}")


def write_jsonl(pairs: list[TrainingPair], path: Path) -> None:
    tmp_path = path.with_suffix(".jsonl.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
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
                "candidate_sources": pair.candidate_sources,
                "gap_score": pair.gap_score,
                "gap_query_score": pair.gap_query_score,
                "embedding_score": pair.embedding_score,
                "popularity_rank": pair.popularity_rank,
                "is_annotated": pair.is_annotated,
                "split": pair.split,
            }, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


# -----------------------------------------------------------------------------
# CLI entry
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate cross-encoder training pairs."
    )
    parser.add_argument("--n-users", type=int, default=DEFAULT_N_USERS)
    parser.add_argument("--heldout-per-user", type=int, default=DEFAULT_HELDOUT_PER_USER)
    parser.add_argument(
        "--random-negatives-per-positive", type=int,
        default=DEFAULT_RANDOM_NEGATIVES_PER_POSITIVE,
    )
    parser.add_argument(
        "--hard-negatives-per-source", type=int,
        default=DEFAULT_HARD_NEGATIVES_PER_SOURCE,
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_PATH,
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate in memory but skip writing JSONL.",
    )
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    engine = create_engine(db_url)
    with Session(engine) as session:
        pairs, stats = generate_pairs(
            session=session,
            n_users=args.n_users,
            heldout_per_user=args.heldout_per_user,
            random_negatives_per_positive=args.random_negatives_per_positive,
            hard_negatives_per_source=args.hard_negatives_per_source,
            seed=args.seed,
        )
        print_stats(stats)
        print(f"\nTotal pairs generated: {len(pairs)}")

        if args.dry_run:
            print("(--dry-run: JSONL not written)")
        elif pairs:
            write_jsonl(pairs, args.output)
            print(f"Wrote {len(pairs)} pairs -> {args.output}")
        else:
            print("(no pairs generated)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
