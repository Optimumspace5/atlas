"""Stage 0 of the corpus quality audit: deterministic duplicate detection.

Groups near-identical books in data/corpus_merged_v1.csv by a normalized
(title, first-author-surname) key, picks one canonical representative per
group (richest rating/metadata), and writes data/corpus_dedup_groups_v1.csv.

Exact-ISBN duplicates were already collapsed during the corpus merge
(see the dedup_key column); this catches title-level / cross-edition
duplicates that share an ISBN-distinct entry.

NO network calls. NO mutation of the corpus or annotation files — output
is a brand-new file. Feeds the evidence + judging stages and lets the
curated-catalog step keep one book per duplicate group.

corpus_row is the 1-based data-row position in corpus_merged_v1.csv,
matching the corpus_row convention in data/annotations_v1.csv.

Usage:
    python scripts/dedup_corpus.py
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
OUT_CSV = Path("data/corpus_dedup_groups_v1.csv")

# Whole-word removals before building the dedup key (editions, articles, filler).
_NOISE_WORDS = {
    "the", "a", "an", "and", "of", "for", "to", "in", "on",
    "edition", "revised", "updated", "expanded", "new", "complete",
    "fully", "classic", "anniversary", "reprint",
    "first", "second", "third", "fourth", "fifth", "sixth",
    "volume", "vol", "part",
}
_EDITION_NUM = re.compile(r"\b\d+(st|nd|rd|th)\b")


def normalize_title(title: str) -> str:
    """Lowercase, drop subtitle, strip punctuation + edition/filler words."""
    t = (title or "").strip().lower()
    t = t.split(":")[0]                 # main title carries identity
    t = _EDITION_NUM.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    tokens = [w for w in t.split() if w and w not in _NOISE_WORDS]
    return " ".join(tokens)


def author_surname(author: str) -> str:
    """First author's surname, lowercased."""
    a = (author or "").strip()
    if not a:
        return ""
    first = re.split(r",| and |&", a, maxsplit=1)[0].strip()
    parts = first.split()
    return parts[-1].lower() if parts else ""


def dedup_key(title: str, author: str) -> str:
    return f"{normalize_title(title)}|{author_surname(author)}"


def _canonical_sort_key(row: dict) -> tuple:
    """Higher = more canonical: rating present, more reviews, fuller
    metadata, then earliest year as a mild tiebreak."""
    avg = row.get("avg_rating", "").strip()
    cnt = row.get("ratings_count", "").strip()
    try:
        cnt_n = int(cnt) if cnt else 0
    except ValueError:
        cnt_n = 0
    try:
        year_n = int(row.get("publication_year", "").strip() or 0)
    except ValueError:
        year_n = 0
    has_desc = 1 if row.get("description", "").strip() else 0
    has_pub = 1 if row.get("publisher", "").strip() else 0
    return (1 if avg else 0, cnt_n, has_desc, has_pub, -year_n if year_n else 0)


def main() -> int:
    if not CORPUS_CSV.exists():
        print(f"ERROR: {CORPUS_CSV} not found")
        return 2

    with CORPUS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows, start=1):
        row["_corpus_row"] = i
        row["_key"] = dedup_key(row.get("title", ""), row.get("author", ""))

    # Group by normalized (title, author surname). Empty title+author -> unique.
    key_to_group: dict[str, int] = {}
    groups: list[list[dict]] = []
    for row in rows:
        k = row["_key"]
        if k == "|":
            groups.append([row])
            continue
        if k not in key_to_group:
            key_to_group[k] = len(groups)
            groups.append([])
        groups[key_to_group[k]].append(row)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out_rows, dup_groups = [], 0
    for gid, members in enumerate(groups):
        if len(members) > 1:
            dup_groups += 1
        canonical = max(members, key=_canonical_sort_key)
        for row in members:
            out_rows.append({
                "corpus_row": row["_corpus_row"],
                "google_volume_id": row.get("google_volume_id", ""),
                "title": row.get("title", ""),
                "author": row.get("author", ""),
                "dedup_norm_key": row["_key"],
                "duplicate_group": gid,
                "group_size": len(members),
                "is_canonical": row is canonical,
                "suggested_canonical_title": canonical.get("title", ""),
            })

    out_rows.sort(key=lambda r: r["corpus_row"])
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "corpus_row", "google_volume_id", "title", "author",
            "dedup_norm_key", "duplicate_group", "group_size",
            "is_canonical", "suggested_canonical_title",
        ])
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Read {len(rows)} corpus rows")
    print(f"Grouped into {len(groups)} groups; {dup_groups} have >1 member "
          f"({len(rows) - len(groups)} duplicate rows to collapse)")
    print(f"Wrote {OUT_CSV}")
    big = sorted([g for g in groups if len(g) > 1], key=len, reverse=True)[:10]
    if big:
        print("\nLargest duplicate groups:")
        for g in big:
            print(f"  {len(g)}x  {g[0].get('title', '')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
