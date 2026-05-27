"""Gap-fill synthetic evaluation harness — Atlas baselines.

Generates synthetic users drawn from 4 reading archetypes (value investor,
technical trader, macro thinker, behavioral trader). Each user 'reads' 8
books sampled from their archetype's home parent concepts, then 2 books
are held out from their secondary concepts. The harness measures how well
each ranking strategy surfaces those held-out books in its top-K.

Strategies evaluated:
    - gap        - rank_candidates from gap_scoring.py
    - popularity - rank_by_popularity from popularity.py
    - tfidf      - rank_by_tfidf from tfidf.py

Metric: NDCG@10. Held-out books are binary relevance (1 if recommended,
0 otherwise). Per-user NDCG is averaged across users for each strategy.

Why 'gap-fill' rather than neutral hold-out: this eval tests Atlas's
actual product claim — surfacing adjacent under-covered concepts after
a user has saturated their home area. A neutral random hold-out would
instead test next-book prediction, which is not what gap-scoring is
designed for. A future preference_continuation_eval_v1 will cover that.

Usage:
    python scripts/evaluate_baselines.py

Requires DATABASE_URL in env. Writes MLflow runs to ./mlruns/ under
experiment 'gap_fill_eval_v1'.
"""
import math
import os
import random
import statistics
import sys
import uuid
from pathlib import Path

import mlflow
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402
from backend.app.services.gap_scoring import rank_candidates  # noqa: E402
from backend.app.services.popularity import rank_by_popularity  # noqa: E402
from backend.app.services.tfidf import rank_by_tfidf  # noqa: E402

# --- Eval configuration --------------------------------------------------
RANDOM_SEED = 42
N_USERS_PER_ARCHETYPE = 5
N_READ = 8           # books each synthetic user has 'read' (home-weighted)
N_HELDOUT = 2        # books held out as positives (secondary-weighted)
K = 10               # NDCG@K

# Weight applied to secondary-leaf annotation strength when sampling the
# held-out pool. Home leaves stay at 1.0 — see module docstring.
SECONDARY_WEIGHT = 0.3

MLFLOW_EXPERIMENT = "gap_fill_eval_v1"

# --- Archetype definitions -----------------------------------------------
# Each archetype has a 'home' parent concept (all 6 of its leaves get
# weight 1.0 in the read-book sampling) and a list of 'secondary' leaf
# concepts (each gets weight SECONDARY_WEIGHT in the held-out sampling).
#
# Identifiers are concept slugs (Concept.slug in models.py). Home is a
# parent (level=0) slug; secondary entries are leaf (level=1) slugs.
# Cross-archetype reuse of a secondary leaf is fine — drawdown_management
# appears under both value_investor and behavioral_trader, which is
# realistic (both archetypes care about losing-streak survival).
ARCHETYPES = {
    "value_investor": {
        "home": "fundamental_analysis_and_valuation",
        "secondary": [
            "drawdown_management",
            "business_cycles_and_market_cycles",
            "probabilistic_thinking_and_uncertainty",
            "passive_vs_active_portfolio_management",
        ],
    },
    "technical_trader": {
        "home": "technical_analysis_and_market_structure",
        "secondary": [
            "position_sizing",
            "stop_loss_and_exit_rules",
            "risk_reward_and_expectancy",
            "overtrading_and_impulse_control",
        ],
    },
    "macro_thinker": {
        "home": "macro_cycles_and_economic_context",
        "secondary": [
            "asset_allocation",
            "diversification",
            "market_participants_and_incentives",
            "intrinsic_value_estimation",
        ],
    },
    "behavioral_trader": {
        "home": "trading_psychology_and_behavioral_finance",
        "secondary": [
            "journaling_and_performance_review",
            "drawdown_management",
            "stop_loss_and_exit_rules",
            "system_iteration_and_continuous_improvement",
        ],
    },
}

# --- Per-archetype weight resolution -------------------------------------
def load_archetype_weights(
    session: Session,
    archetype_spec: dict,
) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, float]]:
    """Resolve an archetype's slugs into per-book sampling weights.

    Returns (home_weights, secondary_weights), each {book_id: weight}.
        home_weights[b]      = Σ over home leaves      of annotation_strength(b, leaf)
        secondary_weights[b] = SECONDARY_WEIGHT × Σ over secondary leaves of strength

    Books not annotated on the relevant concepts are absent from the dict
    (treated as weight 0 — they have no chance of being sampled).

    Raises ValueError if the home parent slug or any secondary leaf slug
    is missing from the DB.
    """
    home_parent_id = session.scalar(
        select(Concept.id).where(Concept.slug == archetype_spec["home"])
    )
    if home_parent_id is None:
        raise ValueError(f"Home parent slug not found: {archetype_spec['home']}")

    home_leaf_ids = session.execute(
        select(Concept.id).where(Concept.parent_id == home_parent_id)
    ).scalars().all()

    secondary_leaf_ids = session.execute(
        select(Concept.id).where(Concept.slug.in_(archetype_spec["secondary"]))
    ).scalars().all()
    if len(secondary_leaf_ids) != len(archetype_spec["secondary"]):
        raise ValueError(
            f"Missing secondary slugs for archetype: {archetype_spec['secondary']}"
        )

    def _aggregate(concept_ids: list[uuid.UUID], scale: float) -> dict[uuid.UUID, float]:
        rows = session.execute(
            select(
                BookConceptAnnotation.book_id,
                func.sum(BookConceptAnnotation.strength),
            )
            .where(BookConceptAnnotation.concept_id.in_(concept_ids))
            .group_by(BookConceptAnnotation.book_id)
        ).all()
        return {book_id: scale * float(w) for book_id, w in rows}

    home_weights = _aggregate(home_leaf_ids, 1.0)
    secondary_weights = _aggregate(secondary_leaf_ids, SECONDARY_WEIGHT)
    return home_weights, secondary_weights

# --- Synthetic user generation -------------------------------------------
def _weighted_sample(
    weights: dict[uuid.UUID, float],
    k: int,
    rng: random.Random,
) -> list[uuid.UUID]:
    """Weighted sampling without replacement (Efraimidis-Spirakis algorithm).

    Each key with positive weight gets a sort key u^(1/w) where u ~ U(0,1);
    the top-k by sort key are returned. Equivalent to drawing k items one
    at a time with probability ∝ remaining weights. Returns fewer than k
    items only if the input has fewer than k keys with positive weight.
    """
    sort_keys = []
    for key, w in weights.items():
        if w <= 0:
            continue
        u = rng.random()
        sort_keys.append((u ** (1.0 / w), key))
    sort_keys.sort(reverse=True)
    return [key for _, key in sort_keys[:k]]


def generate_synthetic_user(
    home_weights: dict[uuid.UUID, float],
    secondary_weights: dict[uuid.UUID, float],
    rng: random.Random,
) -> tuple[list[uuid.UUID], list[uuid.UUID]] | None:
    """Generate one synthetic user's (read_ids, heldout_ids) draw.

    Phase 1: sample N_READ books from home_weights.
    Phase 2: sample N_HELDOUT books from secondary_weights, excluding any
             already drawn into the read set.

    Returns None if either phase can't fill its quota — caller skips this
    user with a warning. With pools at 10+ books per archetype (see the
    weights smoke test) this should never trigger in practice.
    """
    read_ids = _weighted_sample(home_weights, N_READ, rng)
    if len(read_ids) < N_READ:
        return None

    read_set = set(read_ids)
    eligible_secondary = {
        b: w for b, w in secondary_weights.items() if b not in read_set
    }
    if len(eligible_secondary) < N_HELDOUT:
        return None

    heldout_ids = _weighted_sample(eligible_secondary, N_HELDOUT, rng)
    return read_ids, heldout_ids

# --- NDCG@K metric -------------------------------------------------------
def ndcg_at_k(
    ranked_ids: list[uuid.UUID],
    relevant_set: set[uuid.UUID],
    k: int = K,
) -> float:
    """Normalized Discounted Cumulative Gain at K, binary relevance.

    DCG = Σ over top-k of rel_i / log2(i+1), positions 1-indexed.
    IDCG = best-case DCG when min(|relevant|, k) positives sit at the top.
    Returns DCG / IDCG ∈ [0, 1]. Returns 0 if no relevant items exist.

    With our setup (|relevant| = 2, k = 10):
        IDCG = 1/log2(2) + 1/log2(3) ≈ 1.6309
        A perfect ranking (both held-outs at positions 1-2) → NDCG = 1.0
        Both at the bottom of top-10 (positions 9-10) → NDCG ≈ 0.39
        Neither in top-10 → NDCG = 0.0
    """
    dcg = 0.0
    for i, book_id in enumerate(ranked_ids[:k], start=1):
        if book_id in relevant_set:
            dcg += 1.0 / math.log2(i + 1)

    n_relevant_in_topk = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_relevant_in_topk + 1))

    return dcg / idcg if idcg > 0 else 0.0

# --- Strategy dispatch + per-user evaluation -----------------------------
def _rank_with_strategy(
    strategy_name: str,
    session: Session,
    read_ids: list[uuid.UUID],
    candidate_pool: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Dispatch to a ranking strategy; return ordered list of book IDs.

    Both strategies return [(Book, score), ...] sorted descending. The
    caller (evaluate_strategy) doesn't need to know which one ran.
    """
    if strategy_name == "gap":
        ranked = rank_candidates(session, read_ids, candidate_pool)
    elif strategy_name == "popularity":
        # popularity takes top_k directly and filters read_ids internally;
        # K is enough for NDCG@K.
        ranked = rank_by_popularity(session, read_ids, top_k=K)
    elif strategy_name == "tfidf":
        # TF-IDF also takes top_k directly and filters read_ids internally.
        ranked = rank_by_tfidf(session, read_ids, top_k=K)
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    return [book.id for book, _ in ranked]


def evaluate_strategy(
    session: Session,
    strategy_name: str,
    synthetic_users: list[dict],
    candidate_pool: list[uuid.UUID],
) -> list[dict]:
    """Score one strategy across all synthetic users; return per-user results.

    Input user dicts:  {archetype, user_idx, read_ids, heldout_ids}.
    Output dicts add  strategy and ndcg_at_10 and are shaped for direct
    MLflow logging in Section 7.
    """
    results = []
    for user in synthetic_users:
        ranked_ids = _rank_with_strategy(
            strategy_name, session, user["read_ids"], candidate_pool,
        )
        ndcg = ndcg_at_k(ranked_ids, set(user["heldout_ids"]), k=K)
        results.append({
            "archetype": user["archetype"],
            "user_idx": user["user_idx"],
            "strategy": strategy_name,
            "ndcg_at_10": ndcg,
            "n_read": len(user["read_ids"]),
            "n_heldout": len(user["heldout_ids"]),
        })
    return results

# --- Main: build users, run strategies, log to MLflow, print summary -----
def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    rng = random.Random(RANDOM_SEED)
    engine = create_engine(db_url)

    with Session(engine) as session:
        # --- Build N_USERS_PER_ARCHETYPE synthetic users per archetype ---
        users: list[dict] = []
        for archetype_name, spec in ARCHETYPES.items():
            home_w, sec_w = load_archetype_weights(session, spec)
            for user_idx in range(N_USERS_PER_ARCHETYPE):
                result = generate_synthetic_user(home_w, sec_w, rng)
                if result is None:
                    print(f"[SKIP] {archetype_name} user {user_idx}: pool too small")
                    continue
                read_ids, heldout_ids = result
                users.append({
                    "archetype": archetype_name,
                    "user_idx": user_idx,
                    "read_ids": read_ids,
                    "heldout_ids": heldout_ids,
                })
        print(f"Generated {len(users)} synthetic users "
              f"({N_USERS_PER_ARCHETYPE} per archetype × {len(ARCHETYPES)} archetypes).")

        candidate_pool = list(session.execute(select(Book.id)).scalars().all())
        print(f"Candidate pool: {len(candidate_pool)} books.\n")

        # --- Evaluate each strategy ---
        all_results: dict[str, list[dict]] = {}
        for strategy in ("gap", "popularity", "tfidf"):
            print(f"Evaluating {strategy}...")
            results = evaluate_strategy(session, strategy, users, candidate_pool)
            all_results[strategy] = results

            for r in results:
                run_name = f"{r['strategy']}__{r['archetype']}__u{r['user_idx']}"
                with mlflow.start_run(run_name=run_name):
                    mlflow.log_params({
                        "strategy": r["strategy"],
                        "archetype": r["archetype"],
                        "user_idx": r["user_idx"],
                        "seed": RANDOM_SEED,
                        "n_read": r["n_read"],
                        "n_heldout": r["n_heldout"],
                        "k": K,
                    })
                    mlflow.log_metric("ndcg_at_10", r["ndcg_at_10"])
            print(f"  done — {len(results)} runs logged.")

        # --- Per-strategy summary runs + console table ---
        print()
        print(f"{'strategy':<14}{'mean NDCG@10':>16}{'std':>10}{'n_users':>10}")
        print("-" * 50)
        for strategy, results in all_results.items():
            scores = [r["ndcg_at_10"] for r in results]
            mean = statistics.mean(scores) if scores else 0.0
            std = statistics.stdev(scores) if len(scores) > 1 else 0.0

            with mlflow.start_run(run_name=f"{strategy}__summary"):
                mlflow.set_tag("is_summary", "true")
                mlflow.log_params({
                    "strategy": strategy,
                    "seed": RANDOM_SEED,
                    "n_users": len(scores),
                    "k": K,
                })
                mlflow.log_metric("mean_ndcg_at_10", mean)
                mlflow.log_metric("std_ndcg_at_10", std)
                if scores:
                    mlflow.log_metric("min_ndcg_at_10", min(scores))
                    mlflow.log_metric("max_ndcg_at_10", max(scores))

            print(f"{strategy:<14}{mean:>16.4f}{std:>10.4f}{len(scores):>10}")

        print()
        print("MLflow runs at: ./mlruns/  (run `mlflow ui` to browse)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
