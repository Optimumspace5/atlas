"""Backfill missing books from the top-50 curated intake list.

Reads `data/book_intake_top50_v1.csv`, looks up rows marked
`needs_backfill` through Google Books, appends high-confidence matches to
`data/corpus_merged_v1.csv`, and writes low-confidence cases to
`scripts/top50_needs_manual_review.txt`.
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

from backfill_audit_books import (
    _candidate_reason,
    _clean_cell,
    _extract_row_from_volume,
    _language_ok,
    _normalize_title,
    _title_similarity,
    append_to_corpus,
    fetch_lookup_page,
    load_corpus,
    score_candidate,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

INTAKE_CSV = Path("data/book_intake_top50_v1.csv")
CORPUS_CSV = Path("data/corpus_merged_v1.csv")
MANUAL_REVIEW_PATH = Path("scripts/top50_needs_manual_review.txt")

TITLE_SIMILARITY_THRESHOLD = 0.80
EXISTING_TITLE_SIMILARITY_THRESHOLD = 0.85


def _author_surnames(author: Any) -> set[str]:
    """Extract likely surname tokens from a source author field."""
    text = _clean_cell(author)
    text = re.sub(r"\bet\s+al\.?\b", "", text, flags=re.IGNORECASE)
    text = text.replace("/", " and ")
    text = re.sub(r"\b(CFA|Institute|Company|McKinsey)\b", " ", text, flags=re.IGNORECASE)
    parts = re.split(r"\band\b|,|&", text, flags=re.IGNORECASE)
    suffixes = {"jr", "sr", "ii", "iii", "iv"}
    surnames: set[str] = set()
    for part in parts:
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9]+", part)
            if token.lower() not in suffixes
        ]
        if tokens:
            surnames.add(tokens[-1].lower())
    return surnames


def _candidate_author_matches(author_blob: Any, source_author: Any) -> bool:
    """Return True when any source surname appears among candidate authors."""
    surnames = _author_surnames(source_author)
    if not surnames:
        return False
    if isinstance(author_blob, list):
        text = " ".join(str(author) for author in author_blob)
    else:
        text = _clean_cell(author_blob)
    tokens = set(re.findall(r"[A-Za-z0-9]+", text.lower()))
    return bool(surnames & tokens)


def _primary_author_query_token(author: Any) -> str:
    """Return a conservative author token for Google Books inauthor search."""
    text = _clean_cell(author)
    first_author = re.split(r"\band\b|,|&|/", text, maxsplit=1, flags=re.IGNORECASE)[0]
    surnames = _author_surnames(first_author)
    if surnames:
        return sorted(surnames)[0]
    surnames = _author_surnames(author)
    return sorted(surnames)[0] if surnames else ""


def build_lookup_query(title: str, author: str) -> str:
    """Build a Google Books lookup query for a curated book."""
    lookup_title = title
    if ", CFA Institute" in lookup_title:
        lookup_title = lookup_title.split(", CFA Institute", 1)[0]
    escaped_title = lookup_title.replace('"', "")
    author_token = _primary_author_query_token(author).replace('"', "")
    if author_token:
        return f'intitle:"{escaped_title}" inauthor:"{author_token}"'
    return f'intitle:"{escaped_title}"'


def load_needs_backfill(path: Path) -> list[dict[str, Any]]:
    """Load intake rows that still need corpus backfill."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return [
            {key: _clean_cell(value) for key, value in row.items()}
            for row in reader
            if _clean_cell(row.get("status")) == "needs_backfill"
        ]


def is_already_in_corpus(
    title: str,
    author: str,
    corpus_rows: list[dict[str, Any]],
) -> bool:
    """Return True when the curated book likely already exists in corpus."""
    for row in corpus_rows:
        score = _title_similarity(row.get("title", ""), title, row.get("subtitle", ""))
        if score >= EXISTING_TITLE_SIMILARITY_THRESHOLD and _candidate_author_matches(row.get("author"), author):
            return True
    return False


def _full_title_similarity(candidate_title: Any, target_title: Any) -> float:
    """Return normalized full-title similarity."""
    candidate = _normalize_title(candidate_title)
    target = _normalize_title(target_title)
    if not candidate or not target:
        return 0.0
    return SequenceMatcher(None, candidate, target).ratio()


def _not_summary_like(title: Any, subtitle: Any) -> int:
    """Penalize obvious summaries/workbooks/study guides."""
    text = f"{_clean_cell(title)} {_clean_cell(subtitle)}".lower()
    banned = [
        "summary",
        "workbook",
        "study guide",
        "analysis of",
        "companion",
        "key takeaways",
        "minutes",
        "download",
    ]
    return 0 if any(token in text for token in banned) else 1


def candidate_score(volume: dict[str, Any], target_title: str, target_author: str) -> tuple:
    """Return a comparable score for one Google Books candidate."""
    info = volume.get("volumeInfo") or {}
    title = info.get("title") or ""
    subtitle = info.get("subtitle") or ""
    title_similarity = _title_similarity(title, target_title, subtitle)
    full_title_similarity = _full_title_similarity(title, target_title)
    author_match = 1 if _candidate_author_matches(info.get("authors"), target_author) else 0
    language_score = 1 if _language_ok(info.get("language")) else 0
    ratings_count = info.get("ratingsCount")
    if not isinstance(ratings_count, int):
        ratings_count = 0
    published = info.get("publishedDate") or ""
    year_match = re.search(r"\d{4}", str(published))
    publication_year = int(year_match.group()) if year_match else 0
    return (
        full_title_similarity,
        title_similarity,
        author_match,
        language_score,
        _not_summary_like(title, subtitle),
        ratings_count,
        publication_year,
    )


def select_best_match(
    candidates: list[dict[str, Any]],
    target_title: str,
    target_author: str,
) -> Optional[tuple[dict[str, Any], int, tuple]]:
    """Select a high-confidence Google Books candidate."""
    passing: list[tuple[dict[str, Any], int, tuple]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        score = candidate_score(candidate, target_title, target_author)
        if (
            (score[0] >= TITLE_SIMILARITY_THRESHOLD or score[1] >= 0.98)
            and score[2] == 1
            and score[3] == 1
            and score[4] == 1
        ):
            passing.append((candidate, index, score))
    if not passing:
        return None
    return max(passing, key=lambda item: item[2])


def _candidate_debug(candidate: dict[str, Any], title: str, author: str) -> str:
    """Return compact candidate diagnostics for manual review."""
    info = candidate.get("volumeInfo") or {}
    score = candidate_score(candidate, title, author)
    return (
        f"full_title_similarity={score[0]:.3f}, title_similarity={score[1]:.3f}, "
        f"author_match={score[2]}, language_ok={score[3]}, not_summary={score[4]}, "
        f"title={info.get('title')!r}, authors={info.get('authors')!r}"
    )


def backfill_one_book(
    intake_row: dict[str, Any],
    corpus_rows: list[dict[str, Any]],
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Backfill one curated top-50 book."""
    title = intake_row["title"]
    author = intake_row["author"]

    if is_already_in_corpus(title, author, corpus_rows):
        return "already_present", None, None

    query = build_lookup_query(title, author)
    envelope = fetch_lookup_page(query)
    response = envelope["response"]
    candidates = response.get("items") if isinstance(response.get("items"), list) else []

    selected = select_best_match(candidates, title, author)
    if selected is None:
        sorted_candidates = sorted(
            (candidate for candidate in candidates if isinstance(candidate, dict)),
            key=lambda candidate: candidate_score(candidate, title, author),
            reverse=True,
        )
        return (
            "manual_review",
            None,
            {
                "entry": intake_row,
                "query": query,
                "reason": "No candidate met title/author/language/not-summary thresholds.",
                "top_candidates": [
                    _candidate_debug(candidate, title, author)
                    for candidate in sorted_candidates[:3]
                ],
            },
        )

    volume, result_index, score = selected
    row = _extract_row_from_volume(
        volume,
        query=query,
        result_index=result_index,
        fetched_at=envelope["fetched_at"],
    )
    if row is None:
        return (
            "manual_review",
            None,
            {
                "entry": intake_row,
                "query": query,
                "reason": "Selected candidate could not be converted into a corpus row.",
                "top_candidates": [_candidate_debug(volume, title, author)],
            },
        )

    row["source"] = "curated_top50"
    row["dedup_strategy"] = "curated_lookup_isbn" if row.get("canonical_isbn_13") else "curated_lookup_volume_id"
    log.info(
        "  Selected: %r by %s (score=%.3f, ratings=%s)",
        row["title"],
        row["author"],
        score[0],
        row.get("ratings_count") or "n/a",
    )
    return "added", row, None


def write_manual_review_list(deferred: list[dict[str, Any]]) -> None:
    """Write low-confidence top-50 lookups for manual review."""
    lines: list[str] = []
    if not deferred:
        lines.append("No manual review needed.")
    for item in deferred:
        entry = item["entry"]
        lines.append(f"Rank {entry['rank']}: {entry['title']} - {entry['author']}")
        lines.append(f"Query: {item.get('query', '')}")
        lines.append(f"Reason: {item['reason']}")
        for rank, candidate in enumerate(item.get("top_candidates", []), start=1):
            lines.append(f"  Candidate {rank}: {candidate}")
        lines.append("")
    MANUAL_REVIEW_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    """Run curated top-50 corpus backfill."""
    intake_rows = load_needs_backfill(INTAKE_CSV)
    corpus_rows, fieldnames = load_corpus(CORPUS_CSV)
    log.info("Loaded %d needs_backfill rows from %s", len(intake_rows), INTAKE_CSV)
    log.info("Loaded %d books from %s", len(corpus_rows), CORPUS_CSV)

    new_rows: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    counts = {"added": 0, "already_present": 0, "manual_review": 0}

    for entry in intake_rows:
        log.info("[%s] Looking up: %s - %s", entry["rank"], entry["title"], entry["author"])
        status, row, deferred_item = backfill_one_book(entry, corpus_rows)
        counts[status] += 1
        if row is not None:
            new_rows.append(row)
            corpus_rows.append(row)
            log.info("  ADDED to corpus at row %d", len(corpus_rows))
        if deferred_item is not None:
            deferred.append(deferred_item)
            log.warning("  Deferred to manual review")

    if new_rows:
        append_to_corpus(new_rows, CORPUS_CSV, fieldnames)
    write_manual_review_list(deferred)

    log.info(
        "Done. %d added, %d already present, %d deferred to manual review.",
        counts["added"],
        counts["already_present"],
        counts["manual_review"],
    )
    log.info("Corpus now has %d rows.", len(corpus_rows))
    log.info("Manual review file: %s", MANUAL_REVIEW_PATH)
    return 0 if not deferred else 1


if __name__ == "__main__":
    sys.exit(main())
