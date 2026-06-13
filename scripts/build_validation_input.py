"""Track B3 (setup): pick the best-annotated manual books to validate the
grounded annotator against gold.

Selects the N books with the most manual/manual_audit annotations (richest gold
coverage) that also have a description in the corpus, and writes them as an
annotate_grounded.py input CSV. Running the grounded annotator on these and
diffing against annotations_v1.csv tells us whether grounding matches/beats the
bulk pass before we spend on the real must-adds.

Output: data/validate_manual.csv (google_volume_id, title, author, description)

Usage:
    python scripts/build_validation_input.py            # top 3
    python scripts/build_validation_input.py --n 5
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

ANNOTATIONS = Path("data/annotations_v1.csv")
CORPUS = Path("data/corpus_merged_v1.csv")
OUT = Path("data/validate_manual.csv")
MANUAL_TYPES = {"manual", "manual_audit"}


def _read(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=3)
    args = parser.parse_args()

    counts: Counter = Counter()
    for a in _read(ANNOTATIONS):
        if a.get("annotation_type") in MANUAL_TYPES:
            vid = (a.get("google_volume_id") or "").strip()
            if vid:
                counts[vid] += 1

    corpus = {(r.get("google_volume_id") or "").strip(): r for r in _read(CORPUS)}

    picked = []
    for vid, n in counts.most_common():
        b = corpus.get(vid)
        if b and (b.get("description") or "").strip():
            picked.append((vid, n, b))
        if len(picked) >= args.n:
            break

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["google_volume_id", "title", "author", "description"])
        w.writeheader()
        for vid, n, b in picked:
            w.writerow({
                "google_volume_id": vid,
                "title": b.get("title", ""),
                "author": b.get("author", ""),
                "description": b.get("description", ""),
            })
            print(f"  {n:>3} gold anns | {b.get('title','')[:48]:<48} | {b.get('author','')[:22]}")

    print(f"\nWrote {len(picked)} validation books -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
