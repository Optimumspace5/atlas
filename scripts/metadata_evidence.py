"""Track A, Stage A4 (part 1): build metadata-only evidence for the NEEDS_JUDGE
books so the existing judge can rule on them WITHOUT web search.

Reads the prefilter partition + the corpus, and for every book in the
NEEDS_JUDGE bucket emits one record in the EXACT schema judge_corpus_quality.py
expects -- but with web_evidence=[] and source="metadata_only". The judge then
rules from title/author/publisher/description (+ any Google Books rating the
corpus happens to carry). This is cheaper but weaker than web evidence; the
intent is to confidently DROP obvious scrape junk and KEEP clearly-credible
books, routing the genuine middle to REVIEW_C.

Every record is tagged source="metadata_only" so the audit CSV always shows
which verdicts had no web evidence behind them.

Inputs (read-only):
  data/corpus_prefilter_v1.csv   (bucket == NEEDS_JUDGE selects the targets)
  data/corpus_merged_v1.csv      (the metadata; corpus_row = 1-based)
  data/annotations_v1.csv        (existing_annotation_count)

Output:
  data/corpus_metadata_evidence_v1.jsonl

Usage:
    python scripts/metadata_evidence.py
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PREFILTER_CSV = Path("data/corpus_prefilter_v1.csv")
CORPUS_CSV = Path("data/corpus_merged_v1.csv")
ANNOTATIONS_CSV = Path("data/annotations_v1.csv")
OUT_JSONL = Path("data/corpus_metadata_evidence_v1.jsonl")

DESC_MAX = 600


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _to_float(raw: str):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _to_int(raw: str):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def main() -> int:
    prefilter = _read_csv(PREFILTER_CSV)
    if not prefilter:
        print(f"ERROR: {PREFILTER_CSV} not found or empty (run prefilter_corpus.py first)")
        return 2
    corpus = _read_csv(CORPUS_CSV)
    if not corpus:
        print(f"ERROR: {CORPUS_CSV} not found or empty")
        return 2

    needs_judge_rows = {
        (r.get("corpus_row") or "").strip()
        for r in prefilter
        if r.get("bucket") == "NEEDS_JUDGE"
    }

    ann_counts: Counter = Counter()
    for a in _read_csv(ANNOTATIONS_CSV):
        gvid = (a.get("google_volume_id") or "").strip()
        if gvid:
            ann_counts[gvid] += 1

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for i, b in enumerate(corpus, 1):           # corpus_row is 1-based
            row = str(i)
            if row not in needs_judge_rows:
                continue
            gvid = (b.get("google_volume_id") or "").strip()
            desc = (b.get("description") or "").strip()
            record = {
                "corpus_row": i,
                "google_volume_id": gvid,
                "title": b.get("title", "") or "",
                "author": b.get("author", "") or "",
                "publisher": b.get("publisher", "") or "",
                "publication_year": b.get("publication_year", "") or "",
                "isbn_13": b.get("isbn_13", "") or "",
                "source": "metadata_only",
                "description": desc[:DESC_MAX],
                "existing_annotation_count": ann_counts.get(gvid, 0),
                "google_books_rating": _to_float(b.get("avg_rating", "")),
                "google_books_ratings_count": _to_int(b.get("ratings_count", "")),
                "web_evidence": [],
                "evidence_confidence": "metadata_only",
                "evidence_notes": (
                    "METADATA-ONLY: no web search performed; judge from catalog "
                    "metadata (title, author, publisher, description) only."
                ),
                "searched_at": now,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} metadata-only evidence records -> {OUT_JSONL}")
    if written != len(needs_judge_rows):
        print(f"  NOTE: {len(needs_judge_rows)} NEEDS_JUDGE rows expected, "
              f"{written} matched in corpus (check for row-index drift).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
