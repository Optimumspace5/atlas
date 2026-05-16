"""Generate the v2 annotation priority list.

The v2 list prioritizes audit anchor books first, then rating-boosted books,
then manually whitelisted specialist texts. It preserves `priority_150_v1.csv`
for provenance and writes the new active list to `data/priority_v2.csv`.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
AUDIT_NOTES = Path("docs/audit_notes.md")
SPECIALIST_WHITELIST_CSV = Path("data/specialist_whitelist_v1.csv")
OUTPUT_CSV = Path("data/priority_v2.csv")


def _clean_cell(value: Any) -> str:
    """Return a stripped CSV cell string."""
    return "" if value is None else str(value).strip()


def _normalize_title(value: Any) -> str:
    """Normalize title-like text for fuzzy matching."""
    text = _clean_cell(value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _title_similarity(candidate_title: str, target_title: str, subtitle: str = "") -> float:
    """Return best candidate-title similarity against a target title."""
    target = _normalize_title(target_title)
    variants = [candidate_title]
    if ":" in candidate_title:
        variants.append(candidate_title.split(":", 1)[0])
    if subtitle:
        variants.append(f"{candidate_title} {subtitle}")
    return max(
        (
            SequenceMatcher(None, _normalize_title(variant), target).ratio()
            for variant in variants
            if _normalize_title(variant)
        ),
        default=0.0,
    )


def _last_name(author: str) -> str:
    """Return a simple author last-name token."""
    tokens = re.findall(r"[A-Za-z0-9]+", author)
    return tokens[-1].lower() if tokens else ""


def _author_matches(candidate_author: Any, target_author: str) -> bool:
    """Return True when the target last name appears in candidate author text."""
    last = _last_name(target_author)
    if not last:
        return False
    tokens = re.findall(r"[A-Za-z0-9]+", _clean_cell(candidate_author).lower())
    return last in tokens


def parse_audit_headings(path: Path) -> list[dict[str, Any]]:
    """Extract audit book headings from audit notes."""
    pattern = re.compile(
        r"^###\s+Book\s+(\d+):\s+(.+?)\s+[\u2013\u2014-]\s+(.+?)\s*$"
    )
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            entries.append(
                {
                    "book_number": int(match.group(1)),
                    "title": match.group(2).strip(),
                    "author": match.group(3).strip(),
                }
            )
    if len(entries) != 17:
        raise RuntimeError(f"Expected 17 audit headings, found {len(entries)}")
    return entries


def load_enriched_corpus(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the enriched corpus with attached corpus row/csv line numbers."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=1):
            cleaned = {key: _clean_cell(value) for key, value in row.items()}
            cleaned["corpus_row"] = str(row_number)
            cleaned["csv_line"] = str(row_number + 1)
            rows.append(cleaned)
    return rows, fieldnames


def load_specialist_whitelist(path: Path, corpus_rows: list[dict[str, Any]]) -> set[int]:
    """Read manually whitelisted corpus row numbers."""
    if not path.exists():
        return set()

    max_row = len(corpus_rows)
    whitelist: set[int] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            raw = _clean_cell(row.get("corpus_row"))
            if not raw:
                continue
            try:
                corpus_row = int(raw)
            except ValueError:
                log.warning("Ignoring invalid whitelist corpus_row=%r", raw)
                continue
            if not 1 <= corpus_row <= max_row:
                log.warning("Whitelist row %d is outside corpus row range 1-%d", corpus_row, max_row)
                continue
            whitelist.add(corpus_row)
    return whitelist


def _parse_float(value: Any) -> Optional[float]:
    """Parse a nullable float from CSV text."""
    text = _clean_cell(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: Any) -> Optional[int]:
    """Parse a nullable integer from CSV text."""
    text = _clean_cell(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def resolve_audit_rows(
    corpus: list[dict[str, Any]],
    audit_books: list[dict[str, Any]],
) -> dict[int, int]:
    """Resolve each audit heading to one best corpus row number."""
    resolved: dict[int, int] = {}
    for audit_book in audit_books:
        matches: list[tuple[float, int]] = []
        for row in corpus:
            score = _title_similarity(row.get("title", ""), audit_book["title"], row.get("subtitle", ""))
            if score >= 0.80 and _author_matches(row.get("author", ""), audit_book["author"]):
                matches.append((score, int(row["corpus_row"])))
        if not matches:
            log.warning("Could not resolve audit book for priority tier: %s", audit_book["title"])
            continue
        best_score, best_row = max(matches, key=lambda item: (item[0], -item[1]))
        resolved[best_row] = int(audit_book["book_number"])
        log.debug("Audit book %s resolved to corpus row %d (score %.3f)", audit_book["title"], best_row, best_score)
    return resolved


def compute_tier(
    row: dict[str, Any],
    whitelist_rows: set[int],
    audit_rows: dict[int, int],
) -> Optional[int]:
    """Return priority tier 0-4, or None when excluded."""
    corpus_row = int(row["corpus_row"])
    avg_rating = _parse_float(row.get("avg_rating"))
    ratings_count = _parse_int(row.get("ratings_count"))

    if row.get("source") == "audit_must_include" or corpus_row in audit_rows:
        row["_audit_order"] = audit_rows.get(corpus_row, 999)
        return 0
    if ratings_count is not None and avg_rating is not None:
        if ratings_count >= 100 and avg_rating >= 4.0:
            return 1
        if ratings_count >= 50 and avg_rating >= 3.8:
            return 2
        if ratings_count >= 10 and avg_rating >= 4.0:
            return 3
    if corpus_row in whitelist_rows:
        return 4
    return None


def _sort_key(row: dict[str, Any]) -> tuple:
    """Return deterministic sort key within a priority tier."""
    tier = int(row["priority_tier"])
    avg_rating = _parse_float(row.get("avg_rating"))
    ratings_count = _parse_int(row.get("ratings_count"))
    avg_rating = avg_rating or 0.0
    ratings_count = ratings_count or 0
    if tier == 0:
        return (int(row.get("_audit_order") or 999), int(row["corpus_row"]))
    return (-avg_rating, -ratings_count, _normalize_title(row.get("title")), int(row["corpus_row"]))


def assemble_priority_list(
    corpus: list[dict[str, Any]],
    whitelist_rows: set[int],
    audit_books: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build sorted, tier-ranked priority rows."""
    audit_rows = resolve_audit_rows(corpus, audit_books)
    by_tier: dict[int, list[dict[str, Any]]] = {tier: [] for tier in range(5)}
    excluded = 0

    for row in corpus:
        tier = compute_tier(row, whitelist_rows, audit_rows)
        if tier is None:
            excluded += 1
            continue
        row = dict(row)
        row["priority_tier"] = str(tier)
        by_tier[tier].append(row)

    priority_rows: list[dict[str, Any]] = []
    for tier in range(5):
        tier_rows = sorted(by_tier[tier], key=_sort_key)
        for rank, row in enumerate(tier_rows, start=1):
            row["tier_rank"] = str(rank)
            priority_rows.append(row)
        log.info("Tier %d: %d books", tier, len(tier_rows))

    log.info("Excluded: %d books", excluded)
    return priority_rows


def write_priority_csv(rows: list[dict[str, Any]], path: Path, corpus_fieldnames: list[str]) -> None:
    """Write the priority CSV atomically."""
    fieldnames = ["corpus_row", "csv_line"] + corpus_fieldnames + ["priority_tier", "tier_rank"]
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: "" if row.get(field) is None else row.get(field) for field in fieldnames})
    os.replace(tmp_path, path)


def main() -> int:
    """Generate data/priority_v2.csv."""
    corpus, corpus_fieldnames = load_enriched_corpus(CORPUS_CSV)
    audit_books = parse_audit_headings(AUDIT_NOTES)
    whitelist_rows = load_specialist_whitelist(SPECIALIST_WHITELIST_CSV, corpus)

    log.info("Loaded %d corpus rows", len(corpus))
    log.info("Loaded %d specialist whitelist entries", len(whitelist_rows))

    priority_rows = assemble_priority_list(corpus, whitelist_rows, audit_books)
    write_priority_csv(priority_rows, OUTPUT_CSV, corpus_fieldnames)
    log.info("Total: %d books in %s", len(priority_rows), OUTPUT_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
