"""Phase 5.1 evaluation: cross-encoder vs baselines on the test split.

Per CROSS_ENCODER_DESIGN.md §9. For each test-split user, re-ranks the
full Stage 1 candidate pool (~110 books) with the trained cross-encoder
and computes NDCG@10 against QUALIFIED held-outs (= JSONL positives).
Compares against four baselines: RRF, gap, popularity, embedding_read.

Success criterion: CE mean NDCG@10 >= measured RRF mean NDCG@10 + 0.05.

Safeguards:
  1. Primary NDCG uses QUALIFIED held-outs (JSONL positives), not all raw
     held-outs. Some held-outs failed rule (d') labeling; using them as
     ground truth would penalize the model for our own labeler's calls.
     Secondary NDCG vs all held-outs is logged for context.
  2. Per user, asserts that the regenerated build_user_query() output
     matches the stored JSONL query verbatim. Catches silent drift
     between training/eval/production query format.

Outputs:
  - data/evaluate_cross_encoder_v1.csv (per-user, all baselines)
  - MLflow experiment cross_encoder_eval_v1
  - Console summary

Usage:
    python scripts/evaluate_cross_encoder.py
    python scripts/evaluate_cross_encoder.py --model models/cross_encoder_v1_epoch2

Requires DATABASE_URL in env.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
import uuid
from collections import defaultdict
from pathlib import Path

import mlflow
from sentence_transformers import CrossEncoder
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book  # noqa: E402
from backend.app.services.candidate_generation import (  # noqa: E402
    Candidate,
    generate_candidates,
    reciprocal_rank_fusion,
)
from backend.app.services.query_builder import (  # noqa: E402
    build_candidate_text,
    build_user_query,
)
from scripts.generate_training_data import (  # noqa: E402
    assign_split,
    generate_synthetic_users,
    hold_out,
)


DEFAULT_MODEL = REPO_ROOT / "models" / "cross_encoder_v1_epoch2"
DEFAULT_JSONL = REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl"
DEFAULT_CSV_OUT = REPO_ROOT / "data" / "evaluate_cross_encoder_v1.csv"
MLFLOW_EXPERIMENT = "cross_encoder_eval_v1"
NDCG_K = 10
SUCCESS_DELTA = 0.05

# Must match the values used in the generate_training_data.py run that
# produced cross_encoder_pairs_v1.jsonl — otherwise user regeneration
# diverges and the query-match safeguard catches it.
TRAINING_N_USERS = 250
TRAINING_HELDOUT_PER_USER = 3
TRAINING_SEED = 42


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def ndcg_at_k(
    ranked_ids: list[uuid.UUID],
    relevant_ids: set[uuid.UUID],
    k: int = NDCG_K,
) -> float:
    if not relevant_ids:
        return 0.0
    dcg = 0.0
    for i, bid in enumerate(ranked_ids[:k], start=1):
        if bid in relevant_ids:
            dcg += 1.0 / math.log2(i + 1)
    n_rel = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_rel + 1))
    return dcg / idcg if idcg > 0 else 0.0


def load_test_positives(jsonl_path: Path) -> dict[str, list[dict]]:
    """{user_id: [positive pair dicts]} for users in the test split."""
    by_user: dict[str, list[dict]] = defaultdict(list)
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            if p["split"] != "test":
                continue
            if p["label"] == 1:
                by_user[p["user_id"]].append(p)
    return dict(by_user)


def load_test_query_per_user(jsonl_path: Path) -> dict[str, str]:
    """{user_id: query} — first query encountered per test user."""
    queries: dict[str, str] = {}
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = json.loads(line)
            if p["split"] != "test":
                continue
            queries.setdefault(p["user_id"], p["query"])
    return queries


def baseline_ordering(pool: list[Candidate], rank_attr: str) -> list[uuid.UUID]:
    """Sort pool by a single-source rank ASC (None -> last). Returns book_ids."""
    indexed = [
        (getattr(c, rank_attr) if getattr(c, rank_attr) is not None else 1_000_000, c)
        for c in pool
    ]
    indexed.sort(key=lambda t: t[0])
    return [c.book_id for _, c in indexed]


def rrf_ordering(pool: list[Candidate]) -> list[uuid.UUID]:
    return [c.book_id for c in reciprocal_rank_fusion(pool)]


def ce_ordering(
    model: CrossEncoder,
    query: str,
    pool: list[Candidate],
    books_by_id: dict[uuid.UUID, Book],
) -> list[uuid.UUID]:
    pairs: list[tuple[str, str]] = []
    cands: list[Candidate] = []
    for c in pool:
        book = books_by_id.get(c.book_id)
        if book is None:
            continue
        pairs.append((query, build_candidate_text(book)))
        cands.append(c)
    if not pairs:
        return []
    scores = model.predict(pairs, show_progress_bar=False)
    ranked = sorted(zip(scores, cands), key=lambda t: -float(t[0]))
    return [c.book_id for _, c in ranked]


def fetch_books(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, Book]:
    if not ids:
        return {}
    rows = session.execute(select(Book).where(Book.id.in_(ids))).scalars().all()
    return {b.id: b for b in rows}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 5.1 -- evaluate cross-encoder vs RRF on test split."
    )
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--csv-out", type=Path, default=DEFAULT_CSV_OUT)
    parser.add_argument("--n-users", type=int, default=TRAINING_N_USERS)
    parser.add_argument("--heldout-per-user", type=int, default=TRAINING_HELDOUT_PER_USER)
    parser.add_argument("--seed", type=int, default=TRAINING_SEED)
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2
    if not args.model.exists():
        print(f"ERROR: model not found at {args.model}")
        return 2
    if not args.jsonl.exists():
        print(f"ERROR: training data not found at {args.jsonl}")
        return 2

    dataset_sha256 = hash_file(args.jsonl)
    test_positives = load_test_positives(args.jsonl)
    test_queries = load_test_query_per_user(args.jsonl)
    print(f"Loaded JSONL (sha256={dataset_sha256[:16]}...)")
    print(f"Test users with positives: {len(test_positives)} of {len(test_queries)}")

    print(f"Loading cross-encoder from {args.model} ...")
    model = CrossEncoder(str(args.model))

    print("Connecting to DB + regenerating synthetic users ...")
    engine = create_engine(db_url)
    with Session(engine) as session:
        all_users = generate_synthetic_users(session, args.n_users, args.seed)
        test_users = [
            u for u in all_users
            if assign_split(u.user_id) == "test" and u.user_id in test_positives
        ]
        print(f"Test users to evaluate: {len(test_users)}\n")
        if not test_users:
            print("ERROR: no test users found -- check seed/n_users match training")
            return 2

        results: list[dict] = []
        skipped_query_mismatch = 0

        for i, user in enumerate(test_users, 1):
            kept, held_out = hold_out(user, args.heldout_per_user, args.seed)
            if not kept:
                continue

            # SAFEGUARD 2: assert query matches JSONL
            regenerated_query = build_user_query(session, kept)
            stored_query = test_queries[user.user_id]
            if regenerated_query != stored_query:
                skipped_query_mismatch += 1
                print(f"  [{i}/{len(test_users)}] {user.user_id}: QUERY MISMATCH (skipped)")
                print(f"      stored:      {stored_query!r}")
                print(f"      regenerated: {regenerated_query!r}")
                continue

            pool = generate_candidates(session, kept)
            if not pool:
                continue
            pool_ids = [c.book_id for c in pool]
            books_by_id = fetch_books(session, pool_ids)

            # SAFEGUARD 1: qualified held-outs = JSONL positives only
            qualified_held_out = {
                uuid.UUID(p["candidate_book_id"])
                for p in test_positives[user.user_id]
            }
            all_held_out = set(held_out)

            ce_ranked = ce_ordering(model, regenerated_query, pool, books_by_id)
            rrf_ranked = rrf_ordering(pool)
            gap_ranked = baseline_ordering(pool, "gap_rank")
            pop_ranked = baseline_ordering(pool, "popularity_rank")
            emb_ranked = baseline_ordering(pool, "embedding_rank")

            ndcg_ce = ndcg_at_k(ce_ranked, qualified_held_out)
            ndcg_rrf = ndcg_at_k(rrf_ranked, qualified_held_out)
            ndcg_gap = ndcg_at_k(gap_ranked, qualified_held_out)
            ndcg_pop = ndcg_at_k(pop_ranked, qualified_held_out)
            ndcg_emb = ndcg_at_k(emb_ranked, qualified_held_out)
            ndcg_ce_all = ndcg_at_k(ce_ranked, all_held_out)
            ndcg_rrf_all = ndcg_at_k(rrf_ranked, all_held_out)

            results.append({
                "user_id": user.user_id,
                "archetype": user.archetype,
                "pool_size": len(pool),
                "n_qualified_heldout": len(qualified_held_out),
                "n_all_heldout": len(all_held_out),
                "ndcg_ce": ndcg_ce,
                "ndcg_rrf": ndcg_rrf,
                "ndcg_gap": ndcg_gap,
                "ndcg_pop": ndcg_pop,
                "ndcg_emb": ndcg_emb,
                "ndcg_ce_all": ndcg_ce_all,
                "ndcg_rrf_all": ndcg_rrf_all,
                "ce_lift_vs_rrf": ndcg_ce - ndcg_rrf,
            })
            print(
                f"  [{i}/{len(test_users)}] {user.user_id:<38s} "
                f"CE={ndcg_ce:.3f}  RRF={ndcg_rrf:.3f}  lift={ndcg_ce - ndcg_rrf:+.3f}"
            )

        if skipped_query_mismatch:
            print(f"\nWARNING: {skipped_query_mismatch} users skipped due to query mismatch")

    if not results:
        print("ERROR: no users evaluated")
        return 2

    # ---- Aggregate ----
    def mean(xs):
        return statistics.mean(xs) if xs else 0.0
    def stdev(xs):
        return statistics.stdev(xs) if len(xs) > 1 else 0.0

    mean_ce = mean([r["ndcg_ce"] for r in results])
    mean_rrf = mean([r["ndcg_rrf"] for r in results])
    mean_gap = mean([r["ndcg_gap"] for r in results])
    mean_pop = mean([r["ndcg_pop"] for r in results])
    mean_emb = mean([r["ndcg_emb"] for r in results])
    std_ce = stdev([r["ndcg_ce"] for r in results])
    std_rrf = stdev([r["ndcg_rrf"] for r in results])

    ce_lift = mean_ce - mean_rrf
    target = mean_rrf + SUCCESS_DELTA
    gate_passed = mean_ce >= target

    print()
    print("=" * 72)
    print("PHASE 5.1 RESULTS -- full RRF pool eval on test split")
    print("=" * 72)
    print(f"Test users evaluated:              {len(results)}")
    print(f"Mean pool size:                    {mean([r['pool_size'] for r in results]):.1f}")
    print(f"Mean qualified held-outs per user: {mean([r['n_qualified_heldout'] for r in results]):.2f}")
    print()
    print("NDCG@10 (primary: qualified held-outs):")
    print(f"  cross-encoder:   {mean_ce:.4f}  (std {std_ce:.4f})")
    print(f"  RRF baseline:    {mean_rrf:.4f}  (std {std_rrf:.4f})")
    print(f"  gap (single):    {mean_gap:.4f}")
    print(f"  popularity:      {mean_pop:.4f}")
    print(f"  embedding_read:  {mean_emb:.4f}")
    print()
    print(f"CE lift vs RRF:    {ce_lift:+.4f}")
    print(f"Success target:    >= {target:.4f}  (RRF + {SUCCESS_DELTA})")
    print(f"GATE:              {'PASS' if gate_passed else 'FAIL'}")
    print()

    by_arch: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_arch[r["archetype"]].append(r)
    print("Per-archetype NDCG@10:")
    print(f"  {'archetype':<22} {'n':>4} {'CE':>8} {'RRF':>8} {'lift':>8}")
    for arch, rs in sorted(by_arch.items()):
        ce_a = mean([r["ndcg_ce"] for r in rs])
        rrf_a = mean([r["ndcg_rrf"] for r in rs])
        print(f"  {arch:<22} {len(rs):>4} {ce_a:>8.4f} {rrf_a:>8.4f} {ce_a - rrf_a:>+8.4f}")
    print()

    args.csv_out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(results[0].keys())
    with open(args.csv_out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)
    print(f"Per-user CSV: {args.csv_out}")

    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    with mlflow.start_run():
        mlflow.log_params({
            "model_path": str(args.model.relative_to(REPO_ROOT)),
            "dataset_sha256": dataset_sha256,
            "seed": args.seed,
            "n_users_requested": args.n_users,
            "heldout_per_user": args.heldout_per_user,
            "n_test_users_evaluated": len(results),
            "ndcg_k": NDCG_K,
            "success_delta": SUCCESS_DELTA,
        })
        mlflow.log_metric("mean_ndcg_ce", mean_ce)
        mlflow.log_metric("mean_ndcg_rrf", mean_rrf)
        mlflow.log_metric("mean_ndcg_gap", mean_gap)
        mlflow.log_metric("mean_ndcg_pop", mean_pop)
        mlflow.log_metric("mean_ndcg_emb", mean_emb)
        mlflow.log_metric("std_ndcg_ce", std_ce)
        mlflow.log_metric("std_ndcg_rrf", std_rrf)
        mlflow.log_metric("ce_lift_vs_rrf", ce_lift)
        mlflow.log_metric("gate_passed", 1.0 if gate_passed else 0.0)
        for arch, rs in by_arch.items():
            ce_a = mean([r["ndcg_ce"] for r in rs])
            rrf_a = mean([r["ndcg_rrf"] for r in rs])
            mlflow.log_metric(f"ndcg_ce_{arch}", ce_a)
            mlflow.log_metric(f"ndcg_rrf_{arch}", rrf_a)
        mlflow.log_artifact(str(args.csv_out))

    print(f"\nMLflow runs at ./mlruns/  (run `mlflow ui` to browse)")
    return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(main())
