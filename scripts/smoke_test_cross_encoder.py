"""Phase 4.4 smoke test: confirm fine-tuned cross-encoder ranks positives
above hard negatives for the same user/query.

Samples 5 random users from the test split (held back during training),
scores their positives + hard negatives, and reports the score gap.
If positives consistently outscore negatives, Phase 4 is done.
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from sentence_transformers import CrossEncoder

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "cross_encoder_v1_epoch2"
DEFAULT_JSONL = REPO_ROOT / "data" / "cross_encoder_pairs_v1.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--n-users", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    pairs = [json.loads(l) for l in open(args.jsonl, encoding="utf-8") if l.strip()]
    test_pairs = [p for p in pairs if p["split"] == "test"]
    by_user: dict[str, list[dict]] = defaultdict(list)
    for p in test_pairs:
        by_user[p["user_id"]].append(p)

    eligible = [
        uid for uid, ps in by_user.items()
        if any(p["label"] == 1 for p in ps) and any((p.get("negative_type") or "").startswith("hard_") for p in ps)
    ]
    if not eligible:
        print("ERROR: no test users with both positives and hard negatives")
        return 1

    rng = random.Random(args.seed)
    rng.shuffle(eligible)
    sample_users = eligible[:args.n_users]

    print(f"Loading model from {args.model} ...")
    model = CrossEncoder(str(args.model))
    print("OK\n")

    overall_pos_avg = []
    overall_neg_avg = []
    for uid in sample_users:
        user_pairs = by_user[uid]
        positives = [p for p in user_pairs if p["label"] == 1]
        hard_negs = [p for p in user_pairs if (p.get("negative_type") or "").startswith("hard_")]
        query = positives[0]["query"]
        archetype = positives[0]["archetype"]

        pos_inputs = [(query, p["candidate_text"]) for p in positives]
        neg_inputs = [(query, p["candidate_text"]) for p in hard_negs]
        pos_scores = model.predict(pos_inputs, show_progress_bar=False)
        neg_scores = model.predict(neg_inputs, show_progress_bar=False)

        pos_avg = float(sum(pos_scores)) / len(pos_scores)
        neg_avg = float(sum(neg_scores)) / len(neg_scores)
        overall_pos_avg.append(pos_avg)
        overall_neg_avg.append(neg_avg)

        gap = pos_avg - neg_avg
        verdict = "OK" if gap > 0 else "FAIL"
        print(f"USER {uid} ({archetype}):")
        print(f"  query: {query[:100]}...")
        print(f"  positives ({len(positives)}): scores {[f'{s:+.3f}' for s in pos_scores]}  avg {pos_avg:+.3f}")
        print(f"  hard_negs ({len(hard_negs)}): scores {[f'{s:+.3f}' for s in neg_scores]}  avg {neg_avg:+.3f}")
        print(f"  gap (pos - neg): {gap:+.3f}  [{verdict}]")
        print()

    overall_gap = (sum(overall_pos_avg) / len(overall_pos_avg)) - (sum(overall_neg_avg) / len(overall_neg_avg))
    print("=" * 60)
    print(f"OVERALL gap (pos avg - neg avg across {len(sample_users)} users): {overall_gap:+.3f}")
    print(f"VERDICT: {'PASS' if overall_gap > 0 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
