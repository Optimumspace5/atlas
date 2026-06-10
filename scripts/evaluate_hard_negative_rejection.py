"""Phase 5.2: per-negative-type rejection rate.

Per CROSS_ENCODER_DESIGN.md §9.

For each (positive, hard_X) pair from the SAME user in the test split,
scores both with the trained cross-encoder and checks whether the model
ranks positive above hard_X (= correctly rejects the hard negative).

Reports two metrics per hard-negative type:
  - strict rejection rate: positive_score > negative_score
  - margin-0.1 rejection rate: positive_score >= negative_score + margin

Strict measures correctness; margin measures confidence.

Also reports:
  - archetype x negative_type cross-tab
  - mean positive and negative scores per type
  - worst N failures (cases where the model most confidently picked
    the wrong book — the diagnostic gold)

Logs to MLflow experiment cross_encoder_eval_v1 with tag
phase=5.2_hard_negative_rejection.

Usage:
    python scripts/evaluate_hard_negative_rejection.py
    python scripts/evaluate_hard_negative_rejection.py --model models/cross_encoder_v1_epoch2

No DB connection required.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import mlflow
from sentence_transformers import CrossEncoder


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "cross_encoder_v1_epoch2"
DEFAULT_JSONL = REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl"
HARD_TYPES = ["hard_gap", "hard_embedding_read", "hard_popularity"]
DEFAULT_MARGIN = 0.1
MLFLOW_EXPERIMENT = "cross_encoder_eval_v1"
WORST_N = 10


def load_pairs(path: Path) -> list[dict]:
    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def truncate(text: str, limit: int = 70) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5.2 hard-negative rejection rate.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    args = parser.parse_args()

    if not args.model.exists():
        print(f"ERROR: model not found at {args.model}")
        return 2
    if not args.jsonl.exists():
        print(f"ERROR: training data not found at {args.jsonl}")
        return 2

    all_pairs = load_pairs(args.jsonl)
    test_pairs = [p for p in all_pairs if p["split"] == "test"]
    print(f"Loaded {len(all_pairs)} total pairs, {len(test_pairs)} test pairs.")

    print(f"Loading cross-encoder from {args.model} ...")
    model = CrossEncoder(str(args.model))

    print(f"Scoring {len(test_pairs)} test pairs ...")
    inputs = [(p["query"], p["candidate_text"]) for p in test_pairs]
    scores = model.predict(inputs, show_progress_bar=True)
    for p, s in zip(test_pairs, scores):
        p["_ce_score"] = float(s)

    by_user: dict[str, list[dict]] = defaultdict(list)
    for p in test_pairs:
        by_user[p["user_id"]].append(p)

    # Build (positive, hard_X) comparisons per source type
    comparisons_by_type: dict[str, list[dict]] = {t: [] for t in HARD_TYPES}
    for user_id, user_pairs in by_user.items():
        positives = [p for p in user_pairs if p["label"] == 1]
        if not positives:
            continue
        archetype = positives[0]["archetype"]
        for hard_type in HARD_TYPES:
            hard_negs = [p for p in user_pairs if p.get("negative_type") == hard_type]
            for pos in positives:
                for neg in hard_negs:
                    comparisons_by_type[hard_type].append({
                        "user_id": user_id,
                        "archetype": archetype,
                        "positive_score": pos["_ce_score"],
                        "negative_score": neg["_ce_score"],
                        "positive_text": pos["candidate_text"],
                        "negative_text": neg["candidate_text"],
                    })

    # ---- Aggregate per type ----
    print()
    print("=" * 80)
    print("PHASE 5.2 RESULTS -- per-negative-type rejection rate (test split)")
    print("=" * 80)
    print()
    print("Strict rejection: positive_score > negative_score")
    print(f"Margin rejection: positive_score >= negative_score + {args.margin}")
    print()
    print(f"{'negative_type':<24} {'n':>6} {'pos_mean':>10} {'neg_mean':>10} "
          f"{'strict':>8} {'margin':>8}")

    type_stats: dict[str, dict] = {}
    total_n, total_strict, total_margin = 0, 0, 0
    for hard_type in HARD_TYPES:
        comps = comparisons_by_type[hard_type]
        n = len(comps)
        if n == 0:
            type_stats[hard_type] = {
                "n": 0, "strict_rate": 0.0, "margin_rate": 0.0,
                "pos_mean": 0.0, "neg_mean": 0.0,
            }
            print(f"  {hard_type:<22} {n:>6} {'-':>10} {'-':>10} {'-':>8} {'-':>8}")
            continue
        strict = sum(1 for c in comps if c["positive_score"] > c["negative_score"])
        margin = sum(
            1 for c in comps
            if c["positive_score"] >= c["negative_score"] + args.margin
        )
        pos_mean = sum(c["positive_score"] for c in comps) / n
        neg_mean = sum(c["negative_score"] for c in comps) / n
        strict_rate = strict / n
        margin_rate = margin / n
        type_stats[hard_type] = {
            "n": n, "strict_rate": strict_rate, "margin_rate": margin_rate,
            "pos_mean": pos_mean, "neg_mean": neg_mean,
        }
        total_n += n
        total_strict += strict
        total_margin += margin
        print(f"  {hard_type:<22} {n:>6} {pos_mean:>10.4f} {neg_mean:>10.4f} "
              f"{strict_rate:>8.3f} {margin_rate:>8.3f}")
    overall_strict = total_strict / total_n if total_n else 0.0
    overall_margin = total_margin / total_n if total_n else 0.0
    print(f"  {'OVERALL':<22} {total_n:>6} {'':>10} {'':>10} "
          f"{overall_strict:>8.3f} {overall_margin:>8.3f}")

    # ---- Per archetype x type ----
    print()
    print("By archetype:")
    print(f"  {'archetype':<22} {'negative_type':<24} {'n':>6} {'strict':>8} {'margin':>8}")
    by_arch_type: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for hard_type, comps in comparisons_by_type.items():
        for c in comps:
            by_arch_type[(c["archetype"], hard_type)].append(c)
    archetypes = sorted({c["archetype"] for comps in comparisons_by_type.values() for c in comps})

    arch_type_metrics: list[tuple[str, str, int, float, float]] = []
    for arch in archetypes:
        for hard_type in HARD_TYPES:
            comps = by_arch_type.get((arch, hard_type), [])
            n = len(comps)
            if n == 0:
                continue
            strict = sum(1 for c in comps if c["positive_score"] > c["negative_score"])
            margin = sum(
                1 for c in comps
                if c["positive_score"] >= c["negative_score"] + args.margin
            )
            strict_rate = strict / n
            margin_rate = margin / n
            arch_type_metrics.append((arch, hard_type, n, strict_rate, margin_rate))
            print(f"  {arch:<22} {hard_type:<24} {n:>6} {strict_rate:>8.3f} {margin_rate:>8.3f}")

    # ---- Worst N failures ----
    print()
    print(f"Worst {WORST_N} failures (negative scored highest above positive):")
    print()
    all_failures = []
    for hard_type in HARD_TYPES:
        for c in comparisons_by_type[hard_type]:
            if c["negative_score"] > c["positive_score"]:
                all_failures.append({
                    **c,
                    "negative_type": hard_type,
                    "delta": c["negative_score"] - c["positive_score"],
                })
    all_failures.sort(key=lambda c: -c["delta"])

    if not all_failures:
        print("  (no failures -- every comparison was strictly rejected)")
    else:
        for i, fail in enumerate(all_failures[:WORST_N], 1):
            print(f"  [{i}] {fail['user_id']} ({fail['archetype']}) {fail['negative_type']}")
            print(f"      POS ({fail['positive_score']:+.4f}): {truncate(fail['positive_text'])}")
            print(f"      NEG ({fail['negative_score']:+.4f}): {truncate(fail['negative_text'])}")
            print(f"      delta: {fail['delta']:+.4f}")
            print()

    # ---- MLflow ----
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run():
        mlflow.set_tag("phase", "5.2_hard_negative_rejection")
        mlflow.log_params({
            "model_path": str(args.model.relative_to(REPO_ROOT)),
            "jsonl": str(args.jsonl.relative_to(REPO_ROOT)),
            "margin_threshold": args.margin,
            "n_test_pairs": len(test_pairs),
        })
        for hard_type in HARD_TYPES:
            stats = type_stats[hard_type]
            mlflow.log_metric(f"strict_rejection_rate_{hard_type}", stats["strict_rate"])
            mlflow.log_metric(f"margin_rejection_rate_{hard_type}", stats["margin_rate"])
            mlflow.log_metric(f"pos_mean_score_{hard_type}", stats["pos_mean"])
            mlflow.log_metric(f"neg_mean_score_{hard_type}", stats["neg_mean"])
            mlflow.log_metric(f"n_comparisons_{hard_type}", stats["n"])
        mlflow.log_metric("strict_rejection_rate_overall", overall_strict)
        mlflow.log_metric("margin_rejection_rate_overall", overall_margin)
        for arch, hard_type, n, strict_rate, margin_rate in arch_type_metrics:
            arch_clean = arch.replace(" ", "_")
            mlflow.log_metric(f"strict_{arch_clean}_{hard_type}", strict_rate)
            mlflow.log_metric(f"margin_{arch_clean}_{hard_type}", margin_rate)
        mlflow.log_metric("n_failures", len(all_failures))

    print(f"MLflow run logged to experiment {MLFLOW_EXPERIMENT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
