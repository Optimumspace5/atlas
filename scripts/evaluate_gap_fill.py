"""Phase 6.5c: mission-axis evaluation — gap-fill NDCG under the 416-book regime.

Measures what the trajectory evals (evaluate_baselines.py, the §8 recall
preflight) structurally cannot: how much of the user's knowledge gap each
strategy's top-10 actually closes.

PRIMARY metric — sequential_gap_ndcg@10:
    gap = user's initial gap vector
    for each ranked book i:
        gain_i = sum over book concepts of min(strength, current_gap)
        discounted += gain_i / log2(i + 1)
        current_gap[slug] = max(0, current_gap[slug] - strength)  # book is "read"
    normalized against a GREEDY ORACLE that at each rank picks the
    eligible book with the highest current-gap gain.
    Sequential scoring penalizes redundancy: the second chart-patterns
    book gets credit only for whatever gap remains.

SECONDARY metric — static_gap_ndcg@10:
    gain(book) = score vs the INITIAL gap vector (gap_scoring's own
    objective). Known to favor the gap strategy by construction and to
    over-credit redundant books; reported for the comparison's sake.

Also reported: raw sequential gap reduction@10 (undiscounted volume of
gap closed) and per-archetype slices.

HONESTY NOTE: gap is the mission-objective upper baseline here, not a
fair learned-model competitor — it directly optimizes (the static form
of) this metric. The interesting cells are everyone else, especially the
cross-encoder (quantifies how much mission the trajectory-continuer
sacrifices).

SAFEGUARD: fails loudly if the cross-encoder model is missing. The
production service silently falls back to RRF; an eval must never score
fallback behavior under the cross_encoder label.

Usage:
    python scripts/evaluate_gap_fill.py

Requires DATABASE_URL in env, Postgres up, and the trained model at
models/cross_encoder_v1_epoch2 (or ATLAS_CROSS_ENCODER_MODEL_PATH).
"""
import csv
import math
import os
import random
import statistics
import sys
import uuid
from collections import defaultdict
from pathlib import Path

import mlflow
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402
from backend.app.services.candidate_generation import (  # noqa: E402
    generate_candidates,
    reciprocal_rank_fusion,
)
from backend.app.services.embedding import rank_by_embedding  # noqa: E402
from backend.app.services.gap_scoring import (  # noqa: E402
    get_gap_vector,
    rank_candidates,
    score_candidate,
)
from backend.app.services.popularity import rank_by_popularity  # noqa: E402
from backend.app.services.tfidf import rank_by_tfidf  # noqa: E402
from scripts.evaluate_baselines import (  # noqa: E402
    ARCHETYPES,
    N_USERS_PER_ARCHETYPE,
    RANDOM_SEED,
    generate_synthetic_user,
    load_archetype_weights,
)

K = 10
MLFLOW_EXPERIMENT = "gap_fill_eval_v2"
CSV_OUT = REPO_ROOT / "data" / "evaluate_gap_fill_v2.csv"
STRATEGIES = ["gap", "popularity", "tfidf", "embedding", "rrf", "cross_encoder"]


# -----------------------------------------------------------------------------
# In-memory gain math (mirrors gap_scoring.score_candidate; drift-guarded)
# -----------------------------------------------------------------------------
def fetch_all_annotations(session: Session) -> dict[uuid.UUID, list[tuple[str, float]]]:
    """{book_id: [(slug, strength), ...]} for the whole corpus, one query."""
    rows = session.execute(
        select(
            BookConceptAnnotation.book_id,
            Concept.slug,
            BookConceptAnnotation.strength,
        ).join(Concept, BookConceptAnnotation.concept_id == Concept.id)
    ).all()
    out: dict[uuid.UUID, list[tuple[str, float]]] = defaultdict(list)
    for bid, slug, strength in rows:
        out[bid].append((slug, float(strength)))
    return dict(out)


def gain_in_memory(
    annotations: list[tuple[str, float]], gap: dict[str, float]
) -> float:
    """sum of min(strength, gap) — same math as gap_scoring.score_candidate."""
    return sum(min(s, gap.get(slug, 0.0)) for slug, s in annotations)


def apply_book(
    annotations: list[tuple[str, float]], gap: dict[str, float]
) -> None:
    """Mutate gap as if the user read this book (coverage adds strength)."""
    for slug, s in annotations:
        if slug in gap:
            gap[slug] = max(0.0, gap[slug] - s)


def assert_gain_matches_service(
    session: Session,
    all_annotations: dict[uuid.UUID, list[tuple[str, float]]],
    gap: dict[str, float],
    n_checks: int = 3,
) -> None:
    """Drift guard: in-memory gain must equal score_candidate exactly."""
    rng = random.Random(0)
    annotated_ids = list(all_annotations.keys())
    for bid in rng.sample(annotated_ids, min(n_checks, len(annotated_ids))):
        mem = gain_in_memory(all_annotations[bid], gap)
        svc = score_candidate(session, bid, gap)
        if abs(mem - svc) > 1e-9:
            raise AssertionError(
                f"in-memory gain drifted from score_candidate for {bid}: "
                f"{mem} != {svc}"
            )


# -----------------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------------
def sequential_metrics(
    ranked_ids: list[uuid.UUID],
    initial_gap: dict[str, float],
    all_annotations: dict[uuid.UUID, list[tuple[str, float]]],
    oracle_discounted: float,
    k: int = K,
) -> tuple[float, float]:
    """Returns (sequential_gap_ndcg, raw_gap_reduction) for one ranking."""
    gap = dict(initial_gap)
    discounted = 0.0
    raw = 0.0
    for i, bid in enumerate(ranked_ids[:k], start=1):
        anns = all_annotations.get(bid, [])
        g = gain_in_memory(anns, gap)
        discounted += g / math.log2(i + 1)
        raw += g
        apply_book(anns, gap)
    ndcg = discounted / oracle_discounted if oracle_discounted > 0 else 0.0
    return ndcg, raw


def greedy_oracle_discounted(
    eligible_ids: list[uuid.UUID],
    initial_gap: dict[str, float],
    all_annotations: dict[uuid.UUID, list[tuple[str, float]]],
    k: int = K,
) -> float:
    """Best-case discounted sequential gain: at each rank, pick the
    eligible book with the highest gain against the CURRENT gap."""
    gap = dict(initial_gap)
    remaining = [bid for bid in eligible_ids if bid in all_annotations]
    discounted = 0.0
    for i in range(1, k + 1):
        if not remaining:
            break
        best_bid, best_gain = None, 0.0
        for bid in remaining:
            g = gain_in_memory(all_annotations[bid], gap)
            if g > best_gain:
                best_bid, best_gain = bid, g
        if best_bid is None or best_gain <= 0.0:
            break
        discounted += best_gain / math.log2(i + 1)
        apply_book(all_annotations[best_bid], gap)
        remaining.remove(best_bid)
    return discounted


def static_gap_ndcg(
    ranked_ids: list[uuid.UUID],
    initial_gap: dict[str, float],
    all_annotations: dict[uuid.UUID, list[tuple[str, float]]],
    eligible_ids: list[uuid.UUID],
    k: int = K,
) -> float:
    """Graded NDCG with gains fixed against the INITIAL gap vector."""
    static_gain = {
        bid: gain_in_memory(all_annotations.get(bid, []), initial_gap)
        for bid in eligible_ids
    }
    dcg = sum(
        static_gain.get(bid, 0.0) / math.log2(i + 1)
        for i, bid in enumerate(ranked_ids[:k], start=1)
    )
    ideal = sorted(static_gain.values(), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg > 0 else 0.0


# -----------------------------------------------------------------------------
# Strategy runners — each returns top-K book_ids for one user
# -----------------------------------------------------------------------------
def run_strategy(
    name: str,
    session: Session,
    read_ids: list[uuid.UUID],
    all_book_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    if name == "gap":
        ranked = rank_candidates(session, read_ids, all_book_ids)
        return [b.id for b, _ in ranked[:K]]
    if name == "popularity":
        return [b.id for b, _ in rank_by_popularity(session, read_ids, K)]
    if name == "tfidf":
        return [b.id for b, _ in rank_by_tfidf(session, read_ids, K)]
    if name == "embedding":
        return [b.id for b, _ in rank_by_embedding(session, read_ids, K)]
    if name == "rrf":
        pool = generate_candidates(session, read_ids)
        return [c.book_id for c in reciprocal_rank_fusion(pool)[:K]]
    if name == "cross_encoder":
        from backend.app.services.reranker import rank_by_cross_encoder
        return [b.id for b, _ in rank_by_cross_encoder(session, read_ids, K)]
    raise ValueError(f"unknown strategy {name}")


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    # SAFEGUARD: the eval must never silently score the production RRF
    # fallback as "cross_encoder". Fail loudly if the model can't load.
    from backend.app.services import reranker
    if reranker._get_model() is None:
        print(f"ERROR: cross-encoder model not loadable from {reranker.MODEL_PATH}.")
        print("Refusing to evaluate fallback behavior under the cross_encoder label.")
        return 2
    print("Cross-encoder model loaded (fail-loud check passed).")

    rng = random.Random(RANDOM_SEED)
    engine = create_engine(db_url)

    with Session(engine) as session:
        all_annotations = fetch_all_annotations(session)
        all_book_ids = list(session.execute(select(Book.id)).scalars().all())
        print(f"Corpus: {len(all_book_ids)} books, {len(all_annotations)} annotated.")

        # Same users as the trajectory evals (same generator, same seed).
        users = []
        for archetype_name, spec in ARCHETYPES.items():
            home_w, sec_w = load_archetype_weights(session, spec)
            for user_idx in range(N_USERS_PER_ARCHETYPE):
                result = generate_synthetic_user(home_w, sec_w, rng)
                if result is None:
                    continue
                read_ids, heldout_ids = result
                users.append({
                    "archetype": archetype_name,
                    "user_idx": user_idx,
                    "read_ids": list(read_ids) + list(heldout_ids),
                })
        # NOTE: held-outs are irrelevant on this axis (no identity matching);
        # we fold them into the reading history so the user state matches
        # what a real user with that full history would look like.
        print(f"Generated {len(users)} synthetic users.\n")

        # Drift guard before any scoring.
        sample_gap = get_gap_vector(session, users[0]["read_ids"])
        assert_gain_matches_service(session, all_annotations, sample_gap)
        print("Drift guard: in-memory gain == score_candidate. OK\n")

        results: list[dict] = []
        for ui, user in enumerate(users, 1):
            read_ids = user["read_ids"]
            read_set = set(read_ids)
            eligible = [bid for bid in all_book_ids if bid not in read_set]
            initial_gap = get_gap_vector(session, read_ids)
            total_gap = sum(initial_gap.values())
            oracle = greedy_oracle_discounted(eligible, initial_gap, all_annotations)

            for strat in STRATEGIES:
                ranked = run_strategy(strat, session, read_ids, all_book_ids)
                seq_ndcg, raw = sequential_metrics(
                    ranked, initial_gap, all_annotations, oracle
                )
                st_ndcg = static_gap_ndcg(
                    ranked, initial_gap, all_annotations, eligible
                )
                results.append({
                    "archetype": user["archetype"],
                    "user_idx": user["user_idx"],
                    "strategy": strat,
                    "sequential_gap_ndcg": seq_ndcg,
                    "static_gap_ndcg": st_ndcg,
                    "raw_gap_reduction": raw,
                    "total_initial_gap": total_gap,
                })
            print(f"  [{ui}/{len(users)}] {user['archetype']} u{user['user_idx']} done")

    # ---- Aggregate ----
    def rows_for(strat):
        return [r for r in results if r["strategy"] == strat]

    print()
    print("=" * 78)
    print("PHASE 6.5c — GAP-FILL (MISSION-AXIS) EVAL, 416-book annotation regime")
    print("=" * 78)
    print()
    print("gap = mission-objective upper baseline (optimizes this metric's")
    print("static form directly); not a fair learned-model competitor.")
    print()
    print(f"{'strategy':<16} {'seq_ndcg@10':>12} {'std':>8} {'static_ndcg':>12} "
          f"{'raw_reduction':>14} {'% gap closed':>13}")
    for strat in STRATEGIES:
        rs = rows_for(strat)
        seq = statistics.mean(r["sequential_gap_ndcg"] for r in rs)
        seq_std = statistics.stdev([r["sequential_gap_ndcg"] for r in rs]) if len(rs) > 1 else 0.0
        st = statistics.mean(r["static_gap_ndcg"] for r in rs)
        raw = statistics.mean(r["raw_gap_reduction"] for r in rs)
        pct = statistics.mean(
            r["raw_gap_reduction"] / r["total_initial_gap"] * 100
            for r in rs if r["total_initial_gap"] > 0
        )
        print(f"{strat:<16} {seq:>12.4f} {seq_std:>8.4f} {st:>12.4f} "
              f"{raw:>14.2f} {pct:>12.1f}%")

    print()
    print("Per-archetype sequential_gap_ndcg@10:")
    archetypes = sorted({r["archetype"] for r in results})
    header = f"{'strategy':<16}" + "".join(f"{a[:18]:>20}" for a in archetypes)
    print(header)
    for strat in STRATEGIES:
        line = f"{strat:<16}"
        for a in archetypes:
            rs = [r for r in results if r["strategy"] == strat and r["archetype"] == a]
            line += f"{statistics.mean(r['sequential_gap_ndcg'] for r in rs):>20.4f}"
        print(line)

    # ---- CSV + MLflow ----
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_OUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nPer-user CSV: {CSV_OUT}")

    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name="summary"):
        mlflow.set_tag("phase", "6.5c_mission_axis")
        mlflow.log_params({
            "annotation_regime": "416_books_auto_v1",
            "n_users": len({(r['archetype'], r['user_idx']) for r in results}),
            "seed": RANDOM_SEED,
            "k": K,
        })
        for strat in STRATEGIES:
            rs = rows_for(strat)
            mlflow.log_metric(f"seq_gap_ndcg_{strat}",
                              statistics.mean(r["sequential_gap_ndcg"] for r in rs))
            mlflow.log_metric(f"static_gap_ndcg_{strat}",
                              statistics.mean(r["static_gap_ndcg"] for r in rs))
            mlflow.log_metric(f"raw_reduction_{strat}",
                              statistics.mean(r["raw_gap_reduction"] for r in rs))
        mlflow.log_artifact(str(CSV_OUT))
    print("MLflow: gap_fill_eval_v2 summary logged.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
