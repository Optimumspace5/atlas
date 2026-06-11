"""Phase 6.5d: the missing two-axis cell — cross-encoder trajectory NDCG
under the 416-book annotation regime.

Same 20 synthetic users as evaluate_baselines.py (same generator, same
seed), same metric (held-out books as binary relevance, NDCG@10), but
ranked by the production cross-encoder pipeline (rank_by_cross_encoder:
Stage 1 pool -> RRF -> top-50 rerank).

Unlike evaluate_gap_fill.py, held-outs are NOT folded into the reading
history here — they are the prediction targets, exactly as in the
trajectory baselines.

Comparison rows (already measured under this regime):
    gap 0.000 / popularity 0.000 / tfidf 0.013 / embedding 0.081 (6.5b)
    RRF pool ordering 0.000 (6.5a preflight)

SAFEGUARD: fails loudly if the cross-encoder model is missing — never
scores the production RRF fallback under the cross_encoder label.

Usage:
    python scripts/evaluate_ce_trajectory.py

Requires DATABASE_URL, Postgres up, trained model on disk.
"""
import os
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import mlflow
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluate_baselines import (  # noqa: E402
    ARCHETYPES,
    N_USERS_PER_ARCHETYPE,
    RANDOM_SEED,
    generate_synthetic_user,
    load_archetype_weights,
    ndcg_at_k,
)

K = 10
MLFLOW_EXPERIMENT = "cross_encoder_eval_v1"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    # Fail-loud: never evaluate the RRF fallback as "cross_encoder".
    from backend.app.services import reranker
    if reranker._get_model() is None:
        print(f"ERROR: cross-encoder model not loadable from {reranker.MODEL_PATH}.")
        return 2
    print("Cross-encoder model loaded (fail-loud check passed).")

    rng = random.Random(RANDOM_SEED)
    engine = create_engine(db_url)

    results: list[dict] = []
    with Session(engine) as session:
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
                    "read_ids": list(read_ids),
                    "heldout_ids": list(heldout_ids),
                })
        print(f"Generated {len(users)} synthetic users.\n")

        for i, user in enumerate(users, 1):
            ranked = reranker.rank_by_cross_encoder(
                session, user["read_ids"], K,
            )
            ranked_ids = [b.id for b, _ in ranked]
            ndcg = ndcg_at_k(ranked_ids, set(user["heldout_ids"]), k=K)
            results.append({
                "archetype": user["archetype"],
                "user_idx": user["user_idx"],
                "ndcg_at_10": ndcg,
            })
            print(f"  [{i}/{len(users)}] {user['archetype']} u{user['user_idx']}: "
                  f"NDCG@10 = {ndcg:.4f}")

    scores = [r["ndcg_at_10"] for r in results]
    mean = statistics.mean(scores)
    std = statistics.stdev(scores) if len(scores) > 1 else 0.0

    print()
    print("=" * 70)
    print("PHASE 6.5d — cross_encoder TRAJECTORY NDCG@10, 416-book regime")
    print("=" * 70)
    print(f"\n  mean NDCG@10: {mean:.4f}  (std {std:.4f}, n={len(scores)})\n")
    print("  Comparison (same regime, same users):")
    print("    embedding   0.0807")
    print("    tfidf       0.0132")
    print("    gap         0.0000")
    print("    popularity  0.0000")
    print("    RRF (pool)  0.0000")
    print()
    print("  Per-archetype:")
    by_arch: dict[str, list[float]] = defaultdict(list)
    for r in results:
        by_arch[r["archetype"]].append(r["ndcg_at_10"])
    for arch in sorted(by_arch):
        print(f"    {arch:<22} {statistics.mean(by_arch[arch]):.4f}")

    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run(run_name="ce_trajectory_416_regime"):
        mlflow.set_tag("phase", "6.5d_trajectory_416")
        mlflow.log_params({
            "annotation_regime": "416_books_auto_v1",
            "n_users": len(results),
            "seed": RANDOM_SEED,
            "k": K,
        })
        mlflow.log_metric("mean_ndcg_at_10", mean)
        mlflow.log_metric("std_ndcg_at_10", std)
        for arch, vals in by_arch.items():
            mlflow.log_metric(f"ndcg_{arch}", statistics.mean(vals))
    print("\nMLflow: logged to cross_encoder_eval_v1.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
