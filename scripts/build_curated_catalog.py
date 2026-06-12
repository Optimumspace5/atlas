"""Track A, Stage A5: build the curated catalog (deterministic, no API).

Merges the corpus metadata + prefilter buckets + judge verdicts + dedup groups
into the final curated catalog. Keep rule:

    KEEP  if AUTO_KEEP (manual annotation or pinned anchor)  -- always wins
          OR audit quality_tier in {KEEP_A, KEEP_B}
    REVIEW if audit quality_tier == REVIEW_C (held out, not in catalog)
    DROP   if audit quality_tier == DROP_D   (not copied; recoverable from audit)

Kept books are then collapsed by duplicate_group to one canonical row each
(preferring the flagged-canonical / highest-tier member).

Inputs (read-only):
  data/corpus_merged_v1.csv
  data/corpus_prefilter_v1.csv
  data/corpus_quality_audit_v1.csv
  data/corpus_dedup_groups_v1.csv

Outputs:
  data/curated_core_catalog_v2.csv   (KEEP only, deduped, traceable keep_source)
  data/review_c_candidates_v1.csv    (held-out REVIEW_C pile to mine later)

Usage:
    python scripts/build_curated_catalog.py
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
PREFILTER_CSV = Path("data/corpus_prefilter_v1.csv")
AUDIT_CSV = Path("data/corpus_quality_audit_v1.csv")
DEDUP_CSV = Path("data/corpus_dedup_groups_v1.csv")

CATALOG_CSV = Path("data/curated_core_catalog_v2.csv")
REVIEW_CSV = Path("data/review_c_candidates_v1.csv")

CATALOG_FIELDS = [
    "corpus_row", "google_volume_id", "title", "subtitle", "author",
    "isbn_13", "canonical_isbn_13", "description", "publisher",
    "publication_year", "categories", "language", "cover_url",
    "quality_tier", "relevance_tier", "keep_source",
    "duplicate_group", "collapsed_count",
]
REVIEW_FIELDS = [
    "corpus_row", "google_volume_id", "title", "author", "publisher",
    "publication_year", "source", "reason", "red_flags",
]

# Score for picking the survivor within a duplicate group (higher wins).
SOURCE_SCORE = {
    "pin": 5.0,
    "web_keep_a": 4.0,
    "metadata_keep_a": 4.0,
    "auto_keep_annotation": 3.0,
    "web_keep_b": 2.0,
    "metadata_keep_b": 2.0,
}


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def main() -> int:
    corpus = _read_csv(CORPUS_CSV)
    if not corpus:
        print(f"ERROR: {CORPUS_CSV} not found or empty")
        return 2

    prefilter = {(r.get("corpus_row") or "").strip(): r for r in _read_csv(PREFILTER_CSV)}
    audit = {(r.get("corpus_row") or "").strip(): r for r in _read_csv(AUDIT_CSV)}
    dedup = {(r.get("corpus_row") or "").strip(): r for r in _read_csv(DEDUP_CSV)}

    kept: list[dict] = []      # {meta..., keep_source, score, dup_group}
    review_rows: list[dict] = []
    decision_counts: Counter = Counter()
    keep_source_counts: Counter = Counter()

    for i, b in enumerate(corpus, 1):              # corpus_row is 1-based
        row = str(i)
        pf = prefilter.get(row, {})
        au = audit.get(row, {})
        dd = dedup.get(row, {})
        bucket = pf.get("bucket", "")
        pf_reason = pf.get("reason", "")
        tier = au.get("quality_tier", "")
        source = au.get("source", "")
        dup_group = (dd.get("duplicate_group") or row).strip() or row

        # --- decide keep / review / drop ---
        keep_source = ""
        if bucket == "AUTO_KEEP":
            keep_source = "pin" if "pinned" in pf_reason else "auto_keep_annotation"
            decision = "KEEP"
            eff_tier = tier or "AUTO_KEEP"
        elif tier in ("KEEP_A", "KEEP_B"):
            prefix = "metadata" if source == "metadata_only" else "web"
            keep_source = f"{prefix}_{tier.lower()}"
            decision = "KEEP"
            eff_tier = tier
        elif tier == "REVIEW_C":
            decision = "REVIEW"
        elif tier == "DROP_D":
            decision = "DROP"
        else:
            # No verdict and not auto-kept: never silently keep or drop.
            decision = "REVIEW"

        decision_counts[decision] += 1

        if decision == "REVIEW":
            review_rows.append({
                "corpus_row": row,
                "google_volume_id": b.get("google_volume_id", ""),
                "title": b.get("title", ""),
                "author": b.get("author", ""),
                "publisher": b.get("publisher", ""),
                "publication_year": b.get("publication_year", ""),
                "source": source,
                "reason": au.get("reason", "") or "(no verdict; routed to review)",
                "red_flags": au.get("red_flags", ""),
            })
            continue
        if decision == "DROP":
            continue

        # decision == KEEP
        keep_source_counts[keep_source] += 1
        score = SOURCE_SCORE.get(keep_source, 1.0) + (0.5 if _truthy(dd.get("is_canonical", "")) else 0.0)
        kept.append({
            "meta": b,
            "corpus_row": row,
            "keep_source": keep_source,
            "quality_tier": eff_tier,
            "relevance_tier": au.get("relevance_tier", ""),
            "dup_group": dup_group,
            "score": score,
        })

    # --- collapse duplicate groups: one survivor per group ---
    groups: dict[str, list[dict]] = defaultdict(list)
    for k in kept:
        groups[k["dup_group"]].append(k)

    survivors: list[dict] = []
    collapsed_dups = 0
    for grp, members in groups.items():
        members.sort(key=lambda m: (-m["score"], int(m["corpus_row"])))
        winner = members[0]
        winner["collapsed_count"] = len(members)
        collapsed_dups += len(members) - 1
        survivors.append(winner)

    survivors.sort(key=lambda m: int(m["corpus_row"]))

    # --- write catalog ---
    CATALOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CATALOG_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        for s in survivors:
            b = s["meta"]
            w.writerow({
                "corpus_row": s["corpus_row"],
                "google_volume_id": b.get("google_volume_id", ""),
                "title": b.get("title", ""),
                "subtitle": b.get("subtitle", ""),
                "author": b.get("author", ""),
                "isbn_13": b.get("isbn_13", ""),
                "canonical_isbn_13": b.get("canonical_isbn_13", ""),
                "description": b.get("description", ""),
                "publisher": b.get("publisher", ""),
                "publication_year": b.get("publication_year", ""),
                "categories": b.get("categories", ""),
                "language": b.get("language", ""),
                "cover_url": b.get("cover_url", ""),
                "quality_tier": s["quality_tier"],
                "relevance_tier": s["relevance_tier"],
                "keep_source": s["keep_source"],
                "duplicate_group": s["dup_group"],
                "collapsed_count": s["collapsed_count"],
            })

    with REVIEW_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        w.writeheader()
        w.writerows(review_rows)

    # --- report ---
    print(f"Catalog -> {CATALOG_CSV}")
    print(f"Review  -> {REVIEW_CSV}\n")
    print(f"  decisions over {sum(decision_counts.values())} books: {dict(decision_counts)}")
    print(f"  kept before dedup : {len(kept)}")
    print(f"  duplicates merged : {collapsed_dups}")
    print(f"  CATALOG SIZE      : {len(survivors)}")
    print(f"  review_c held out : {len(review_rows)}\n")
    print("  keep_source breakdown (pre-dedup):")
    for k, v in sorted(keep_source_counts.items(), key=lambda x: -x[1]):
        print(f"    {k:<22} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
