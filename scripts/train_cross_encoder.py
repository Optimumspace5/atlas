"""Fine-tune BAAI/bge-reranker-base on Atlas cross-encoder training pairs.

Phase 4 per docs/CROSS_ENCODER_DESIGN.md.

Reads data/cross_encoder_pairs_v1.jsonl, partitions by 'split' field,
trains with BCE loss using sentence-transformers CrossEncoder.fit(),
checkpoints best by val MRR@10, then computes val NDCG@10 on the best
checkpoint. Logs hyperparams + dataset hash + seeds + metrics to MLflow
experiment cross_encoder_train_v1.

NOTE: Val NDCG@10 here is for diagnostics, NOT the §9 success metric.
Val negatives are a small sampled subset of the candidate pool, so this
number is artificially optimistic. The real success criterion is Phase 5
evaluation against the full RRF candidate pool (target: NDCG@10 >= 0.183).

Test split is held back — never touched during training.

Usage:
    python scripts/train_cross_encoder.py
    python scripts/train_cross_encoder.py --epochs 3 --batch-size 8 --lr 2e-5

No DB connection required — works entirely from the local JSONL.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import mlflow
import numpy as np
import torch
from sentence_transformers import CrossEncoder, InputExample
from sentence_transformers.cross_encoder.evaluation import CERerankingEvaluator
from torch.utils.data import DataLoader


REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_JSONL = REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "models" / "cross_encoder_v1"
BASE_MODEL = "BAAI/bge-reranker-base"
MLFLOW_EXPERIMENT = "cross_encoder_train_v1"

DEFAULT_EPOCHS = 3
DEFAULT_BATCH_SIZE = 8
DEFAULT_LR = 2e-5
DEFAULT_WARMUP_RATIO = 0.1
DEFAULT_MAX_LENGTH = 480
DEFAULT_SEED = 42


def set_seeds(seed: int) -> None:
    """Fix all random sources for reproducibility."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def hash_file(path: Path) -> str:
    """SHA256 of file bytes — anchors model artifacts to exact training data."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pairs(path: Path) -> list[dict]:
    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def assert_no_split_leakage(pairs: list[dict]) -> None:
    """Same user_id must always land in the same split — no cross-split
    leakage. Asserts at training start so we catch any future drift in
    generate_training_data.py's hash-partition logic.
    """
    user_to_splits: dict[str, set[str]] = defaultdict(set)
    for p in pairs:
        user_to_splits[p["user_id"]].add(p["split"])
    leaks = {uid: splits for uid, splits in user_to_splits.items() if len(splits) > 1}
    if leaks:
        sample = next(iter(leaks.items()))
        raise RuntimeError(
            f"Split leakage: {len(leaks)} user_ids in multiple splits. "
            f"Example: {sample[0]} -> {sample[1]}"
        )


def build_train_examples(pairs: list[dict]) -> list[InputExample]:
    return [
        InputExample(texts=[p["query"], p["candidate_text"]], label=float(p["label"]))
        for p in pairs
    ]


def build_eval_samples(pairs: list[dict]) -> tuple[list[dict], int]:
    """Group val pairs by (user_id, query). Drop groups lacking pos+neg."""
    groups: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
        lambda: {"positive": [], "negative": []}
    )
    for p in pairs:
        key = (p["user_id"], p["query"])
        bucket = "positive" if p["label"] == 1 else "negative"
        groups[key][bucket].append(p["candidate_text"])

    samples: list[dict] = []
    skipped = 0
    for (_uid, query), bucket in groups.items():
        if not bucket["positive"] or not bucket["negative"]:
            skipped += 1
            continue
        samples.append({
            "query": query,
            "positive": bucket["positive"],
            "negative": bucket["negative"],
        })
    return samples, skipped


def compute_ndcg_at_k(model: CrossEncoder, samples: list[dict], k: int = 10) -> float:
    """Per-group NDCG@k, averaged. For each group: score all candidates,
    rank, compute DCG / IDCG with binary relevance."""
    if not samples:
        return 0.0
    ndcgs: list[float] = []
    for sample in samples:
        query = sample["query"]
        candidates = sample["positive"] + sample["negative"]
        labels = [1] * len(sample["positive"]) + [0] * len(sample["negative"])
        pairs_in = [(query, c) for c in candidates]
        scores = model.predict(pairs_in, show_progress_bar=False)
        ranked = sorted(zip(scores, labels), key=lambda x: -x[0])
        ranked_labels = [lbl for _, lbl in ranked]
        dcg = sum(
            rel / math.log2(i + 2)
            for i, rel in enumerate(ranked_labels[:k])
        )
        n_rel = min(sum(labels), k)
        ideal = sum(1.0 / math.log2(i + 2) for i in range(n_rel))
        ndcgs.append(dcg / ideal if ideal > 0 else 0.0)
    return sum(ndcgs) / len(ndcgs)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fine-tune bge-reranker-base on Atlas pairs."
    )
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-model", default=BASE_MODEL)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=DEFAULT_LR)
    parser.add_argument("--warmup-ratio", type=float, default=DEFAULT_WARMUP_RATIO)
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    set_seeds(args.seed)

    if not args.jsonl.exists():
        print(f"ERROR: training data not found at {args.jsonl}")
        return 2

    # ---- Load + hash ----
    print(f"Loading {args.jsonl} ...")
    pairs = load_pairs(args.jsonl)
    dataset_sha256 = hash_file(args.jsonl)
    print(f"Loaded {len(pairs)} pairs (sha256={dataset_sha256[:16]}...)")

    # ---- Split leakage check ----
    assert_no_split_leakage(pairs)
    print("Split leakage check: PASS")

    # ---- Partition ----
    train_pairs = [p for p in pairs if p["split"] == "train"]
    val_pairs = [p for p in pairs if p["split"] == "val"]
    test_pairs = [p for p in pairs if p["split"] == "test"]
    print(f"Train: {len(train_pairs)}  Val: {len(val_pairs)}  Test: {len(test_pairs)} (untouched)")

    train_examples = build_train_examples(train_pairs)
    val_samples, val_skipped = build_eval_samples(val_pairs)
    print(f"Train examples: {len(train_examples)}")
    print(f"Val groups:     {len(val_samples)} (skipped {val_skipped} lacking pos+neg)")

    if not val_samples:
        print("ERROR: no valid val groups — cannot run evaluator")
        return 2

    # ---- Model ----
    print(f"Loading base model: {args.base_model}")
    model = CrossEncoder(
        args.base_model,
        num_labels=1,
        max_length=args.max_length,
    )

    evaluator = CERerankingEvaluator(
        val_samples,
        name="val",
        mrr_at_k=10,
    )

    train_loader = DataLoader(
        train_examples,
        shuffle=True,
        batch_size=args.batch_size,
    )
    warmup_steps = int(len(train_loader) * args.epochs * args.warmup_ratio)

    args.output.mkdir(parents=True, exist_ok=True)

    # ---- MLflow + train ----
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    run_name = f"v1_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    started = time.time()

    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({
            "base_model": args.base_model,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "warmup_ratio": args.warmup_ratio,
            "warmup_steps": warmup_steps,
            "max_length": args.max_length,
            "seed": args.seed,
            "dataset_sha256": dataset_sha256,
            "n_train": len(train_examples),
            "n_val_groups": len(val_samples),
            "n_val_skipped": val_skipped,
            "n_test_untouched": len(test_pairs),
        })

        print(f"\nTraining for {args.epochs} epochs (batch {args.batch_size}, lr {args.lr}) ...")
        model.fit(
            train_dataloader=train_loader,
            evaluator=evaluator,
            epochs=args.epochs,
            warmup_steps=warmup_steps,
            optimizer_params={"lr": args.lr},
            output_path=str(args.output),
            save_best_model=True,
            show_progress_bar=True,
        )

        elapsed = time.time() - started
        print(f"\nTraining done in {elapsed/60:.1f} min")
        mlflow.log_metric("training_duration_seconds", elapsed)

        # SAFETY NET: explicitly persist weights — sentence-transformers v3+
        # save_best_model=True silently does nothing without explicit
        # TrainingArguments wiring. This guarantees model.safetensors lands
        # on disk regardless of which fit() API path was taken.
        print(f"\nSaving model weights to {args.output} ...")
        model.save_pretrained(str(args.output))
        print("Save: OK")


        # ---- Parse MRR@10 history from evaluator CSV ----
        evaluator_csv = args.output / "CERerankingEvaluator_val_results.csv"
        best_mrr = None
        if evaluator_csv.exists():
            import csv
            with open(evaluator_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            for i, row in enumerate(rows):
                mrr_val = row.get("MRR@10") or row.get("mrr@10")
                if mrr_val:
                    mlflow.log_metric("val_mrr_at_10", float(mrr_val), step=i)
            mrr_values = [float(r.get("MRR@10") or 0) for r in rows]
            if mrr_values:
                best_mrr = max(mrr_values)
                mlflow.log_metric("best_val_mrr_at_10", best_mrr)

        # ---- Compute final val NDCG@10 on the best checkpoint ----
        print("\nComputing val NDCG@10 on best checkpoint ...")
        best_model = CrossEncoder(str(args.output), num_labels=1, max_length=args.max_length)
        val_ndcg = compute_ndcg_at_k(best_model, val_samples, k=10)
        mlflow.log_metric("val_ndcg_at_10", val_ndcg)
        print(f"Val NDCG@10 (diagnostic, NOT §9 success metric): {val_ndcg:.4f}")

        # ---- training_info.json ----
        info = {
            "base_model": args.base_model,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "warmup_ratio": args.warmup_ratio,
            "warmup_steps": warmup_steps,
            "max_length": args.max_length,
            "seed": args.seed,
            "dataset": str(args.jsonl.relative_to(REPO_ROOT)),
            "dataset_sha256": dataset_sha256,
            "n_train": len(train_examples),
            "n_val_groups": len(val_samples),
            "n_test_untouched": len(test_pairs),
            "best_val_mrr_at_10": best_mrr,
            "val_ndcg_at_10": val_ndcg,
            "training_duration_seconds": elapsed,
            "trained_at": dt.datetime.utcnow().isoformat() + "Z",
            "note": "val NDCG@10 is diagnostic only; Phase 5 will evaluate against the full RRF candidate pool per CROSS_ENCODER_DESIGN.md section 9.",
        }
        info_path = args.output / "training_info.json"
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        mlflow.log_artifact(str(info_path))

        print(f"\nBest val MRR@10:  {best_mrr}")
        print(f"Val NDCG@10:      {val_ndcg:.4f}  (diagnostic only)")
        print(f"Model saved to:   {args.output}")
        print(f"§9 success bar (Phase 5, full RRF pool): NDCG@10 >= 0.183")

    return 0


if __name__ == "__main__":
    sys.exit(main())


