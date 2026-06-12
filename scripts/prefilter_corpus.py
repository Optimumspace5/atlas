"""Track A, Stage A3: NON-DESTRUCTIVE prefilter of the corpus.

Partitions all books in data/corpus_merged_v1.csv into three buckets so the
expensive judge only pays for books that actually need a verdict. It NEVER
drops anything -- the LLM judge owns every DROP decision (with a written
reason). This step only KEEPS vetted books and ROUTES the rest.

Buckets:
  AUTO_KEEP      - clearly vetted, skip judging:
                     * pinned roadmap anchors (Housel / Bogle / Murphy), or
                     * a REAL manual annotation = (>=2 annotations) OR
                       (any annotation at strength 1.0 confirmed).
                   The count/strength rule deliberately EXCLUDES single-weak
                   smoke-test stubs (e.g. Mastering Value Investing / Benedikt:
                   one 0.5 "smoke test" note -> NOT auto-kept -> flows to judge).
  ALREADY_JUDGED - has a verdict in data/corpus_quality_audit_v1.csv; its tier
                   carries straight through.
  NEEDS_JUDGE    - everything else. This is the ONLY set Stage A4 pays to judge;
                   the printed count is the exact A4 cost driver.

Inputs (read-only, nothing mutated):
  data/corpus_merged_v1.csv          (the 468 books; corpus_row = 1-based)
  data/annotations_v1.csv            (manual / manual_audit gold annotations)
  data/corpus_quality_audit_v1.csv   (judge verdicts so far; joined on corpus_row)

Output:
  data/corpus_prefilter_v1.csv

Usage:
    python scripts/prefilter_corpus.py
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
ANNOTATIONS_CSV = Path("data/annotations_v1.csv")
AUDIT_CSV = Path("data/corpus_quality_audit_v1.csv")
OUT_CSV = Path("data/corpus_prefilter_v1.csv")

# Pinned Tier-1 roadmap anchors (title-substring, author-substring), always KEEP.
PINNED_ANCHORS = [
    ("psychology of money", "housel"),
    ("common sense investing", "bogle"),
    ("technical analysis of the financial markets", "murphy"),
]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def is_pinned(title: str, author: str) -> bool:
    t, a = (title or "").lower(), (author or "").lower()
    return any(ts in t and as_ in a for ts, as_ in PINNED_ANCHORS)


def parse_strength(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_real_annotated(anns: list[tuple[str, float | None]]) -> bool:
    """REAL = (>=2 annotations) OR (any confirmed/1.0). Excludes lone smoke-test
    stubs (a single 0.5 weak annotation)."""
    if len(anns) >= 2:
        return True
    return any(s == 1.0 for _, s in anns)


def main() -> int:
    corpus = _read_csv(CORPUS_CSV)
    if not corpus:
        print(f"ERROR: {CORPUS_CSV} not found or empty")
        return 2
    annotations = _read_csv(ANNOTATIONS_CSV)
    audit = _read_csv(AUDIT_CSV)

    # Index annotations by both google_volume_id and corpus_row (a book is
    # looked up by whichever key the corpus row carries).
    ann_by_gvid: dict[str, list] = defaultdict(list)
    ann_by_row: dict[str, list] = defaultdict(list)
    for a in annotations:
        rec = (a.get("annotation_type", ""), parse_strength(a.get("strength", "")))
        gvid = (a.get("google_volume_id") or "").strip()
        row = (a.get("corpus_row") or "").strip()
        if gvid:
            ann_by_gvid[gvid].append(rec)
        if row:
            ann_by_row[row].append(rec)

    # Index audit verdicts by corpus_row (the audit CSV has no google_volume_id).
    audit_by_row: dict[str, dict] = {}
    for r in audit:
        row = (r.get("corpus_row") or "").strip()
        if row:
            audit_by_row[row] = r

    fieldnames = [
        "corpus_row", "google_volume_id", "title", "author",
        "bucket", "reason", "existing_audit_tier",
    ]
    bucket_counts: Counter = Counter()
    conflicts: list[str] = []

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, b in enumerate(corpus, 1):           # corpus_row is 1-based
            row = str(i)
            gvid = (b.get("google_volume_id") or "").strip()
            title = b.get("title", "") or ""
            author = b.get("author", "") or ""

            anns = ann_by_gvid.get(gvid) or ann_by_row.get(row) or []
            audit_row = audit_by_row.get(row)
            audit_tier = (audit_row or {}).get("quality_tier", "")

            if is_pinned(title, author):
                bucket = "AUTO_KEEP"
                reason = "pinned roadmap anchor"
            elif is_real_annotated(anns):
                strengths = [s for _, s in anns if s is not None]
                bucket = "AUTO_KEEP"
                reason = (f"manual annotation ({len(anns)} anns, "
                          f"max strength {max(strengths) if strengths else 'n/a'})")
                # Surface a vetted-vs-judged disagreement for human eyes.
                if audit_tier == "DROP_D":
                    reason += " | CONFLICT: judge said DROP_D"
                    conflicts.append(f"  row {row}: {title[:50]} (auto-kept but judged DROP_D)")
            elif audit_row is not None:
                bucket = "ALREADY_JUDGED"
                reason = f"judge verdict: {audit_tier} ({audit_row.get('keep_decision','')})"
            else:
                bucket = "NEEDS_JUDGE"
                reason = "no annotation, no verdict yet"

            bucket_counts[bucket] += 1
            writer.writerow({
                "corpus_row": row,
                "google_volume_id": gvid,
                "title": title,
                "author": author,
                "bucket": bucket,
                "reason": reason,
                "existing_audit_tier": audit_tier,
            })

    total = sum(bucket_counts.values())
    print(f"Prefiltered {total} corpus books -> {OUT_CSV}\n")
    for bucket in ("AUTO_KEEP", "ALREADY_JUDGED", "NEEDS_JUDGE"):
        print(f"  {bucket:<14} {bucket_counts.get(bucket, 0)}")
    print(f"\n  NEEDS_JUDGE is the only set Stage A4 pays to judge "
          f"(~${bucket_counts.get('NEEDS_JUDGE', 0) * 0.011:.2f} at sonnet, no web search).")
    if conflicts:
        print(f"\n  {len(conflicts)} auto-keep vs judge CONFLICT(s) to eyeball:")
        for c in conflicts:
            print(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
