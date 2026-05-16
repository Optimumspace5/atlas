"""Backfill audit anchor books into the Atlas merged corpus.

The script parses the 17 audit-book headings in `docs/audit_notes.md`, skips
books already present in `data/corpus_merged_v1.csv`, and looks up missing books
through Google Books title+author queries. High-confidence matches are appended
to the corpus with `source='audit_must_include'`; low-confidence matches are
written to `scripts/needs_manual_review.txt`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

AUDIT_NOTES = Path("docs/audit_notes.md")
CORPUS_CSV = Path("data/corpus_merged_v1.csv")
MANUAL_REVIEW_PATH = Path("scripts/needs_manual_review.txt")
CACHE_DIR = Path("data/cache/google_books/v1")

API_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"
API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY")

REQUEST_DEFAULTS: dict[str, Any] = {
    "printType": "books",
    "langRestrict": "en",
    "orderBy": "relevance",
    "maxResults": 40,
}
RETRY_BACKOFF_SECONDS = [1, 4]
PLAUSIBLE_YEAR_MIN = 1450
PLAUSIBLE_YEAR_MAX = datetime.now(timezone.utc).year + 1
TITLE_SIMILARITY_THRESHOLD = 0.80
EXISTING_TITLE_SIMILARITY_THRESHOLD = 0.85


class GoogleBooksFailure(Exception):
    """Raised when Google Books lookup fails after all retries."""


def parse_audit_headings(path: Path) -> list[dict[str, Any]]:
    """Extract audit book number, title, and author from markdown headings."""
    pattern = re.compile(
        r"^###\s+Book\s+(\d+):\s+(.+?)\s+[\u2013\u2014-]\s+(.+?)\s*$"
    )
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
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


def _clean_cell(value: Any) -> str:
    """Return a stripped string for CSV values."""
    return "" if value is None else str(value).strip()


def load_corpus(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load corpus rows and return rows plus original CSV fieldnames."""
    if not path.exists():
        raise FileNotFoundError(f"Corpus file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows = [{key: _clean_cell(value) for key, value in row.items()} for row in reader]
    return rows, fieldnames


def _normalize_title(value: Any) -> str:
    """Normalize title-like text for conservative fuzzy matching."""
    text = _clean_cell(value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _title_candidates(title: str, subtitle: str = "") -> list[str]:
    """Return title variants to compare against an audit title."""
    variants = [title]
    if ":" in title:
        variants.append(title.split(":", 1)[0])
    if subtitle:
        variants.append(f"{title} {subtitle}")
    return [_normalize_title(item) for item in variants if _normalize_title(item)]


def _title_similarity(candidate_title: str, target_title: str, subtitle: str = "") -> float:
    """Return best normalized-title similarity between candidate and target."""
    target = _normalize_title(target_title)
    if not target:
        return 0.0
    return max(
        (SequenceMatcher(None, variant, target).ratio() for variant in _title_candidates(candidate_title, subtitle)),
        default=0.0,
    )


def _full_title_similarity(candidate_title: str, target_title: str) -> float:
    """Return similarity against the full candidate title only."""
    target = _normalize_title(target_title)
    candidate = _normalize_title(candidate_title)
    if not target or not candidate:
        return 0.0
    return SequenceMatcher(None, candidate, target).ratio()


def last_name_from_author(full_name: str) -> str:
    """Extract a simple author last name from a display name."""
    tokens = re.findall(r"[A-Za-z0-9]+", full_name)
    return tokens[-1].lower() if tokens else ""


def _author_last_name_matches(author_blob: Any, target_last_name: str) -> bool:
    """Return True when target last name appears among candidate authors."""
    if not target_last_name:
        return False
    if isinstance(author_blob, list):
        text = " ".join(str(author) for author in author_blob)
    else:
        text = _clean_cell(author_blob)
    names = re.findall(r"[A-Za-z0-9]+", text.lower())
    return target_last_name.lower() in names


def is_already_in_corpus(
    title: str,
    author_last_name: str,
    corpus_rows: list[dict[str, Any]],
) -> bool:
    """Return True when a likely title+author match already exists."""
    for row in corpus_rows:
        score = _title_similarity(row.get("title", ""), title, row.get("subtitle", ""))
        if score >= EXISTING_TITLE_SIMILARITY_THRESHOLD and _author_last_name_matches(
            row.get("author", ""), author_last_name
        ):
            return True
    return False


def build_lookup_query(title: str, last_name: str) -> str:
    """Build the Google Books title+author lookup query."""
    escaped_title = title.replace('"', "")
    escaped_author = last_name.replace('"', "")
    return f'intitle:"{escaped_title}" inauthor:"{escaped_author}"'


def _canonical_request(query: str, start_index: int) -> dict[str, Any]:
    """Build the deterministic request identity, excluding the API key."""
    return {
        "endpoint": API_ENDPOINT,
        "q": query,
        "startIndex": start_index,
        **REQUEST_DEFAULTS,
    }


def _request_hash(canonical: dict[str, Any]) -> str:
    """Return a stable short hash for a canonical request."""
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:8]


def _query_slug(query: str) -> str:
    """Return a Windows-safe filename slug for a Google Books query."""
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
    return re.sub(r"_+", "_", slug)[:120] or "query"


def _cache_path(query: str, start_index: int) -> Path:
    """Return the cache path for a query page without touching disk."""
    canonical = _canonical_request(query, start_index)
    filename = f"{_query_slug(query)}_{start_index}_{_request_hash(canonical)}.json"
    return CACHE_DIR / filename


def _read_cache(path: Path) -> Optional[dict[str, Any]]:
    """Read a cache envelope, returning None when absent or corrupt."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("Ignoring corrupt cache file: %s", path)
        return None


def _write_cache_atomic(path: Path, envelope: dict[str, Any]) -> None:
    """Write a cache envelope atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _fetch_from_api(query: str, start_index: int) -> dict[str, Any]:
    """Fetch one Google Books result page with retries."""
    if not API_KEY:
        raise RuntimeError("GOOGLE_BOOKS_API_KEY not set. Add it to .env at repo root.")

    params = {
        **REQUEST_DEFAULTS,
        "q": query,
        "startIndex": start_index,
        "key": API_KEY,
    }
    total_attempts = 1 + len(RETRY_BACKOFF_SECONDS)
    last_error = "unknown error"

    for attempt in range(total_attempts):
        try:
            response = requests.get(API_ENDPOINT, params=params, timeout=30)
            if response.status_code == 200:
                return response.json()
            preview = response.text[:200].replace("\n", " ")
            last_error = f"HTTP {response.status_code}: {preview}"
            if response.status_code != 429 and not 500 <= response.status_code < 600:
                raise GoogleBooksFailure(last_error)
        except requests.exceptions.Timeout:
            last_error = "Timeout"
        except requests.exceptions.ConnectionError:
            last_error = "ConnectionError"
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError):
            last_error = "JSONDecodeError"

        if attempt < len(RETRY_BACKOFF_SECONDS):
            backoff = RETRY_BACKOFF_SECONDS[attempt]
            log.warning("Google Books lookup failed: %s; retrying in %ss", last_error, backoff)
            time.sleep(backoff)

    raise GoogleBooksFailure(last_error)


def fetch_lookup_page(query: str) -> dict[str, Any]:
    """Return a cached or fetched Google Books lookup envelope."""
    start_index = 0
    path = _cache_path(query, start_index)
    cached = _read_cache(path)
    if cached is not None and "response" in cached and "fetched_at" in cached:
        log.info("cache HIT q=%r", query)
        return cached

    log.info("cache MISS q=%r -> fetching", query)
    response_json = _fetch_from_api(query, start_index)
    envelope = {
        "cache_version": 1,
        "request": _canonical_request(query, start_index),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status_code": 200,
        "response": response_json,
    }
    _write_cache_atomic(path, envelope)
    return envelope


def _strip_isbn(raw: str) -> str:
    """Strip separators from an ISBN-like string."""
    return re.sub(r"[\s\-.]", "", raw).upper()


def _is_valid_isbn_13(value: str) -> bool:
    """Validate an ISBN-13 check digit."""
    if len(value) != 13 or not value.isdigit():
        return False
    digits = [int(char) for char in value]
    weighted_sum = sum(digit * (1 if idx % 2 == 0 else 3) for idx, digit in enumerate(digits[:12]))
    check = (10 - (weighted_sum % 10)) % 10
    return check == digits[12]


def _is_valid_isbn_10(value: str) -> bool:
    """Validate an ISBN-10 check digit."""
    if len(value) != 10 or not value[:9].isdigit():
        return False
    if value[9] != "X" and not value[9].isdigit():
        return False
    values = [int(char) for char in value[:9]] + [10 if value[9] == "X" else int(value[9])]
    return sum(item * (10 - idx) for idx, item in enumerate(values)) % 11 == 0


def _isbn_10_to_13(isbn_10: str) -> str:
    """Convert a stripped valid ISBN-10 into ISBN-13."""
    body = "978" + isbn_10[:9]
    digits = [int(char) for char in body]
    weighted_sum = sum(digit * (1 if idx % 2 == 0 else 3) for idx, digit in enumerate(digits))
    check = (10 - (weighted_sum % 10)) % 10
    return body + str(check)


def canonicalize_isbn(raw_isbn_13: Optional[str], raw_isbn_10: Optional[str]) -> Optional[str]:
    """Return canonical ISBN-13 from raw ISBN fields, if valid."""
    if raw_isbn_13:
        stripped = _strip_isbn(raw_isbn_13)
        if _is_valid_isbn_13(stripped):
            return stripped
    if raw_isbn_10:
        stripped = _strip_isbn(raw_isbn_10)
        if _is_valid_isbn_10(stripped):
            return _isbn_10_to_13(stripped)
    return None


def _extract_isbns(identifiers: Any) -> tuple[Optional[str], Optional[str]]:
    """Extract raw ISBN-13 and ISBN-10 values from Google identifiers."""
    isbn_13 = None
    isbn_10 = None
    if not isinstance(identifiers, list):
        return isbn_13, isbn_10
    for item in identifiers:
        if not isinstance(item, dict):
            continue
        identifier = item.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        if item.get("type") == "ISBN_13" and isbn_13 is None:
            isbn_13 = identifier
        elif item.get("type") == "ISBN_10" and isbn_10 is None:
            isbn_10 = identifier
    return isbn_13, isbn_10


def _extract_year(raw: Any) -> Optional[int]:
    """Extract a plausible year from a Google Books date value."""
    if not isinstance(raw, str):
        return None
    match = re.search(r"\d{4}", raw)
    if not match:
        return None
    year = int(match.group(0))
    return year if PLAUSIBLE_YEAR_MIN <= year <= PLAUSIBLE_YEAR_MAX else None


def _normalize_cover_url(image_links: Any) -> Optional[str]:
    """Return a preferred cover URL, upgrading Google Books HTTP links."""
    if not isinstance(image_links, dict):
        return None
    url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith("http://") and "books.google.com" in url:
        return "https://" + url[len("http://") :]
    return url


def _language_ok(language: Any) -> bool:
    """Return True for English, English-region, or missing language values."""
    if not isinstance(language, str) or not language.strip():
        return True
    return language.strip().lower().startswith("en")


def _author_precision(authors: Any, target_last_name: str) -> int:
    """Prefer single-author matches to summaries with extra authors."""
    if not isinstance(authors, list) or not authors:
        return 0
    normalized_authors = [set(re.findall(r"[a-z0-9]+", str(author).lower())) for author in authors]
    return 1 if len(normalized_authors) == 1 and target_last_name in normalized_authors[0] else 0


def _not_summary_like(title: Any, subtitle: Any) -> int:
    """Down-rank summaries, synopses, analyses, and study-guide editions."""
    text = f"{_clean_cell(title)} {_clean_cell(subtitle)}".lower()
    banned_terms = [
        "summary",
        "synopsis",
        "analysis",
        "concise",
        "study guide",
        "workbook",
        "review",
    ]
    return 0 if any(term in text for term in banned_terms) else 1


def score_candidate(
    volume: dict[str, Any],
    target_title: str,
    target_last_name: str,
) -> tuple[float, float, int, int, int, int, int, int]:
    """Return the candidate ranking tuple for an audit lookup result."""
    info = volume.get("volumeInfo") or {}
    title = info.get("title", "")
    subtitle = info.get("subtitle", "")
    full_similarity = _full_title_similarity(title, target_title)
    similarity = _title_similarity(title, target_title, subtitle)
    author_match = 1 if _author_last_name_matches(info.get("authors"), target_last_name) else 0
    author_precision = _author_precision(info.get("authors"), target_last_name)
    language_score = 1 if _language_ok(info.get("language")) else 0
    raw_isbn_13, _raw_isbn_10 = _extract_isbns(info.get("industryIdentifiers"))
    has_explicit_isbn_13 = 1 if raw_isbn_13 and _is_valid_isbn_13(_strip_isbn(raw_isbn_13)) else 0
    ratings_count = info.get("ratingsCount")
    if not isinstance(ratings_count, int):
        ratings_count = 0
    publication_year = _extract_year(info.get("publishedDate")) or 0
    return (
        full_similarity,
        similarity,
        author_match,
        author_precision,
        language_score,
        _not_summary_like(title, subtitle),
        has_explicit_isbn_13,
        ratings_count,
        publication_year,
    )


def _candidate_reason(
    volume: dict[str, Any],
    target_title: str,
    target_last_name: str,
) -> str:
    """Return a compact debug description of candidate confidence."""
    info = volume.get("volumeInfo") or {}
    score = score_candidate(volume, target_title, target_last_name)
    return (
        f"full_title_similarity={score[0]:.3f}, title_similarity={score[1]:.3f}, "
        f"author_match={score[2]}, language_ok={score[4]}, title={info.get('title')!r}, "
        f"authors={info.get('authors')!r}"
    )


def select_best_match(
    candidates: list[dict[str, Any]],
    target_title: str,
    target_last_name: str,
) -> Optional[tuple[dict[str, Any], int, tuple[float, float, int, int, int, int, int, int]]]:
    """Select a high-confidence candidate, or None if no candidate qualifies."""
    passing: list[tuple[dict[str, Any], int, tuple[float, float, int, int, int, int, int, int]]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        score = score_candidate(candidate, target_title, target_last_name)
        if (
            score[0] >= TITLE_SIMILARITY_THRESHOLD
            and score[2] == 1
            and score[4] == 1
        ):
            passing.append((candidate, index, score))
    if not passing:
        return None
    return max(passing, key=lambda item: item[2])


def _extract_row_from_volume(
    volume: dict[str, Any],
    *,
    query: str,
    result_index: int,
    fetched_at: str,
) -> Optional[dict[str, Any]]:
    """Convert one Google Books volume into a corpus CSV row."""
    info = volume.get("volumeInfo") or {}
    title = _clean_cell(info.get("title"))
    authors = info.get("authors") if isinstance(info.get("authors"), list) else []
    authors_list = [_clean_cell(author) for author in authors if _clean_cell(author)]
    if not title or not authors_list or not _language_ok(info.get("language")):
        return None

    raw_isbn_13, raw_isbn_10 = _extract_isbns(info.get("industryIdentifiers"))
    isbn_13 = _strip_isbn(raw_isbn_13) if raw_isbn_13 else ""
    isbn_10 = _strip_isbn(raw_isbn_10) if raw_isbn_10 else ""
    canonical = canonicalize_isbn(raw_isbn_13, raw_isbn_10)
    categories = ""
    if isinstance(info.get("categories"), list):
        categories = "|".join(_clean_cell(category) for category in info["categories"] if _clean_cell(category))

    avg_rating = info.get("averageRating")
    ratings_count = info.get("ratingsCount")

    dedup_key = canonical or volume.get("id") or _normalize_title(title)
    dedup_strategy = "audit_lookup_isbn" if canonical else "audit_lookup_volume_id"

    return {
        "google_volume_id": volume.get("id") or "",
        "source_query": query,
        "source_queries": query,
        "source_start_index": 0,
        "source_result_index": result_index,
        "global_rank": result_index,
        "fetched_at": fetched_at,
        "dedup_key": dedup_key,
        "dedup_strategy": dedup_strategy,
        "duplicate_count": 1,
        "title": title,
        "subtitle": _clean_cell(info.get("subtitle")),
        "author": ", ".join(authors_list),
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "canonical_isbn_13": canonical or "",
        "description": _clean_cell(info.get("description")),
        "page_count": info.get("pageCount") if isinstance(info.get("pageCount"), int) and info.get("pageCount") > 0 else "",
        "publication_year": _extract_year(info.get("publishedDate")) or "",
        "published_date_raw": _clean_cell(info.get("publishedDate")),
        "publisher": _clean_cell(info.get("publisher")),
        "categories": categories,
        "language": _clean_cell(info.get("language")),
        "cover_url": _normalize_cover_url(info.get("imageLinks")) or "",
        "source": "audit_must_include",
        "avg_rating": avg_rating if isinstance(avg_rating, (int, float)) else "",
        "ratings_count": ratings_count if isinstance(ratings_count, int) else "",
    }


def append_to_corpus(rows: list[dict[str, Any]], path: Path, fieldnames: list[str]) -> None:
    """Rewrite corpus with appended rows using an atomic CSV write."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        existing_rows = list(reader)

    with tmp_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=fieldnames,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in existing_rows:
            writer.writerow({field: "" if row.get(field) is None else row.get(field) for field in fieldnames})
        for row in rows:
            writer.writerow({field: "" if row.get(field) is None else row.get(field) for field in fieldnames})
    os.replace(tmp_path, path)


def write_manual_review_list(deferred: list[dict[str, Any]]) -> None:
    """Write low-confidence audit lookups for manual review."""
    lines: list[str] = []
    if not deferred:
        lines.append("No manual review needed.")
    for item in deferred:
        entry = item["entry"]
        lines.append(f"Book {entry['book_number']}: {entry['title']} - {entry['author']}")
        lines.append(f"Reason: {item['reason']}")
        for rank, candidate in enumerate(item.get("top_candidates", []), start=1):
            lines.append(f"  Candidate {rank}: {candidate}")
        lines.append("")
    MANUAL_REVIEW_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def backfill_one_book(
    audit_entry: dict[str, Any],
    corpus_rows: list[dict[str, Any]],
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Backfill one audit book. Return status plus optional row/deferred entry."""
    title = audit_entry["title"]
    author = audit_entry["author"]
    last_name = last_name_from_author(author)

    if is_already_in_corpus(title, last_name, corpus_rows):
        return "already_present", None, None

    query = build_lookup_query(title, last_name)
    envelope = fetch_lookup_page(query)
    response = envelope["response"]
    candidates = response.get("items") if isinstance(response.get("items"), list) else []

    selected = select_best_match(candidates, title, last_name)
    if selected is None:
        sorted_candidates = sorted(
            (candidate for candidate in candidates if isinstance(candidate, dict)),
            key=lambda candidate: score_candidate(candidate, title, last_name),
            reverse=True,
        )
        deferred = {
            "entry": audit_entry,
            "reason": "No candidate met title/author/language thresholds.",
            "top_candidates": [
                _candidate_reason(candidate, title, last_name)
                for candidate in sorted_candidates[:3]
            ],
        }
        return "manual_review", None, deferred

    volume, result_index, score = selected
    row = _extract_row_from_volume(
        volume,
        query=query,
        result_index=result_index,
        fetched_at=envelope["fetched_at"],
    )
    if row is None:
        deferred = {
            "entry": audit_entry,
            "reason": "Selected candidate could not be converted into a corpus row.",
            "top_candidates": [_candidate_reason(volume, title, last_name)],
        }
        return "manual_review", None, deferred

    log.info(
        "  Selected: %r by %s (score=%.3f, ratings=%s)",
        row["title"],
        row["author"],
        score[0],
        row.get("ratings_count") or "n/a",
    )
    return "added", row, None


def main() -> int:
    """Run audit-book corpus backfill."""
    audit_entries = parse_audit_headings(AUDIT_NOTES)
    corpus_rows, fieldnames = load_corpus(CORPUS_CSV)
    log.info("Parsed %d audit headings from %s", len(audit_entries), AUDIT_NOTES)
    log.info("Loaded %d books from %s", len(corpus_rows), CORPUS_CSV)

    new_rows: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    counts = {"added": 0, "already_present": 0, "manual_review": 0}

    for entry in audit_entries:
        author_last = last_name_from_author(entry["author"])
        if is_already_in_corpus(entry["title"], author_last, corpus_rows):
            log.info("Skipping (already present): %s - %s", entry["title"], entry["author"])
            counts["already_present"] += 1
            continue

        log.info("Looking up: %s - %s", entry["title"], entry["author"])
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
    return 0 if not deferred else 1


if __name__ == "__main__":
    sys.exit(main())
