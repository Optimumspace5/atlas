"""Fetch raw Google Books metadata for the Atlas investing and trading corpus.

This script queries the Google Books API using taxonomy-aligned search terms,
caches each raw API response on disk, and writes a normalized staging CSV for
later review and database loading. The output is `data/corpus_raw_v1.csv`, while
cached API responses are stored under `data/cache/google_books/v1/`. Run from
the repository root with `python scripts/fetch_books.py` after setting
`GOOGLE_BOOKS_API_KEY` in `.env`.
"""

# standard library imports
import os
import sys
import json
import csv
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterable, Any

# third-party imports
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "GOOGLE_BOOKS_API_KEY not set. Add to .env at repo root."
    )

API_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

CACHE_DIR = Path("data/cache/google_books/v1")
OUTPUT_CSV = Path("data/corpus_raw_v1.csv")

MAX_PAGES = 3
MAX_RESULTS_PER_PAGE = 40

REQUEST_DEFAULTS = {
    "printType": "books",
    "langRestrict": "en",
    "orderBy": "relevance",
    "maxResults": MAX_RESULTS_PER_PAGE,
}

RETRY_BACKOFF_SECONDS = [1, 4]
PLAUSIBLE_YEAR_MIN = 1450
PLAUSIBLE_YEAR_MAX = datetime.now(timezone.utc).year + 1


# ---------------------------------------------------------------------------
# Search queries (locked in COLLECTOR_DESIGN.md)
# ---------------------------------------------------------------------------
QUERIES: list[dict[str, str]] = [
    # Parent-category queries (8)
    {"q": "financial markets trading investing",      "target": "market_foundations",                          "kind": "parent"},
    {"q": "fundamental analysis valuation investing", "target": "fundamental_analysis_and_valuation",          "kind": "parent"},
    {"q": "technical analysis trading markets",       "target": "technical_analysis_and_market_structure",     "kind": "parent"},
    {"q": "risk management trading investing",        "target": "risk_management",                             "kind": "parent"},
    {"q": "asset allocation portfolio management",    "target": "portfolio_construction_and_asset_allocation", "kind": "parent"},
    {"q": "trading psychology behavioral finance",    "target": "trading_psychology_and_behavioral_finance",   "kind": "parent"},
    {"q": "global macro investing economic cycles",   "target": "macro_cycles_and_economic_context",           "kind": "parent"},
    {"q": "trading systems backtesting strategy",     "target": "strategy_systems_and_execution",              "kind": "parent"},
    # Gap-filler queries (3)
    {"q": "market microstructure order execution",    "target": "order_types_and_execution_mechanics",         "kind": "gap_filler"},
    {"q": "financial statement analysis investing",   "target": "financial_statement_analysis",                "kind": "gap_filler"},
    {"q": "trade execution slippage liquidity",       "target": "execution_quality_and_trade_implementation",  "kind": "gap_filler"},
]


# ---------------------------------------------------------------------------
# Cache utilities
# ---------------------------------------------------------------------------
def _canonical_request(query: str, start_index: int) -> dict[str, Any]:
    """Build the deterministic request identity, excluding the API key."""
    return {
        "endpoint": API_ENDPOINT,
        "q": query,
        "startIndex": start_index,
        **REQUEST_DEFAULTS,
    }


def _request_hash(canonical: dict[str, Any]) -> str:
    """Return the short stable hash for a canonical request."""
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:8]


def _query_slug(query: str) -> str:
    """Convert a controlled query string into a filename-safe slug."""
    return query.lower().replace(" ", "_")


def _cache_path(query: str, start_index: int) -> Path:
    """Return the cache path for a query page without touching disk."""
    canonical = _canonical_request(query, start_index)
    request_hash = _request_hash(canonical)
    filename = f"{_query_slug(query)}_{start_index}_{request_hash}.json"
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
    """Write a cache envelope atomically to avoid partial cache files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# HTTP fetch with cache
# ---------------------------------------------------------------------------
class TransientAPIFailure(Exception):
    """Raised when the Google Books API fails after all retries."""


def _fetch_from_api(query: str, start_index: int) -> dict[str, Any]:
    """Fetch a Google Books page, retrying transient failures.

    Raises:
        TransientAPIFailure: when all retries are exhausted.
    """
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

            response_preview = response.text[:200].replace("\n", " ")
            last_error = f"HTTP {response.status_code}: {response_preview}"
            if response.status_code != 429 and not 500 <= response.status_code < 600:
                raise TransientAPIFailure(
                    f"Permanent Google Books API failure for q={query!r} "
                    f"start={start_index}: {last_error}"
                )
        except requests.exceptions.Timeout:
            last_error = "Timeout: request timed out"
        except requests.exceptions.ConnectionError:
            last_error = "ConnectionError: connection failed"
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError):
            last_error = "JSONDecodeError: invalid JSON response"
        except requests.exceptions.RequestException as exc:
            last_error = f"{type(exc).__name__}: request failed"

        if attempt < len(RETRY_BACKOFF_SECONDS):
            backoff = RETRY_BACKOFF_SECONDS[attempt]
            log.warning(
                "Google Books API transient failure q=%r start=%s attempt=%s/%s: "
                "%s; retrying in %ss",
                query,
                start_index,
                attempt + 1,
                total_attempts,
                last_error,
                backoff,
            )
            time.sleep(backoff)

    raise TransientAPIFailure(
        f"Google Books API failed after {total_attempts} attempts for "
        f"q={query!r} start={start_index}: {last_error}"
    )


def fetch_page(query: str, start_index: int) -> dict[str, Any]:
    """Return the cached/fetched envelope for a Google Books query page.

    On cache miss, fetches from the API and writes the response to disk
    atomically. Raises TransientAPIFailure if the network call ultimately fails.
    """
    path = _cache_path(query, start_index)
    cached = _read_cache(path)
    if cached is not None and "response" in cached and "fetched_at" in cached:
        log.info("cache HIT q=%r start=%s", query, start_index)
        return cached
    if cached is not None:
        log.warning("Ignoring cache file missing response/fetched_at: %s", path)

    log.info("cache MISS q=%r start=%s -> fetching", query, start_index)
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


# ---------------------------------------------------------------------------
# ISBN canonicalization
# ---------------------------------------------------------------------------
def _strip_isbn(raw: str) -> str:
    """Strip hyphens, spaces, and dots from an ISBN; uppercase trailing X."""
    return re.sub(r"[\s\-\.]", "", raw).upper()


def _is_valid_isbn_13(stripped: str) -> bool:
    """Validate ISBN-13 check digit."""
    if len(stripped) != 13 or not stripped.isdigit():
        return False
    digits = [int(c) for c in stripped]
    weighted_sum = sum(
        d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12])
    )
    check = (10 - (weighted_sum % 10)) % 10
    return check == digits[12]


def _is_valid_isbn_10(stripped: str) -> bool:
    """Validate ISBN-10 check digit. Trailing X counts as 10."""
    if len(stripped) != 10:
        return False
    if not stripped[:9].isdigit():
        return False
    if stripped[9] != "X" and not stripped[9].isdigit():
        return False
    values = [int(c) for c in stripped[:9]] + [
        10 if stripped[9] == "X" else int(stripped[9])
    ]
    weighted_sum = sum(v * (10 - i) for i, v in enumerate(values))
    return weighted_sum % 11 == 0


def _isbn_10_to_13(isbn_10: str) -> str:
    """Convert a stripped, valid ISBN-10 to ISBN-13."""
    body = "978" + isbn_10[:9]
    digits = [int(c) for c in body]
    weighted_sum = sum(
        d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits)
    )
    check = (10 - (weighted_sum % 10)) % 10
    return body + str(check)


def canonicalize_isbn(
    raw_isbn_13: Optional[str],
    raw_isbn_10: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return canonical ISBN-13 and source: explicit, promoted, or None."""
    if raw_isbn_13:
        stripped = _strip_isbn(raw_isbn_13)
        if _is_valid_isbn_13(stripped):
            return stripped, "explicit"
    if raw_isbn_10:
        stripped = _strip_isbn(raw_isbn_10)
        if _is_valid_isbn_10(stripped):
            return _isbn_10_to_13(stripped), "promoted"
    return None, None


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------
def _extract_year(raw: Optional[str]) -> Optional[int]:
    """Extract a plausible publication year from a raw date string."""
    if not raw:
        return None
    match = re.search(r"\d{4}", raw)
    if not match:
        return None
    year = int(match.group(0))
    if PLAUSIBLE_YEAR_MIN <= year <= PLAUSIBLE_YEAR_MAX:
        return year
    return None


def _extract_isbns(identifiers: Any) -> tuple[Optional[str], Optional[str]]:
    """Extract the first raw ISBN-13 and ISBN-10 from Google identifiers."""
    isbn_13 = None
    isbn_10 = None
    if not isinstance(identifiers, list):
        return isbn_13, isbn_10

    for item in identifiers:
        if not isinstance(item, dict):
            continue
        identifier_type = item.get("type")
        identifier = item.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        if identifier_type == "ISBN_13" and isbn_13 is None:
            isbn_13 = identifier
        elif identifier_type == "ISBN_10" and isbn_10 is None:
            isbn_10 = identifier
    return isbn_13, isbn_10


def _normalize_cover_url(image_links: Any) -> Optional[str]:
    """Return a preferred cover URL, upgrading Google Books HTTP links."""
    if not isinstance(image_links, dict):
        return None
    url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith("http://") and "books.google.com" in url:
        return "https://" + url[len("http://"):]
    return url


def extract_row(
    volume: dict[str, Any],
    *,
    query: str,
    start_index: int,
    result_index: int,
    fetched_at: str,
) -> Optional[dict[str, Any]]:
    """Convert one Google Books volume to a CSV row dict, or None to skip."""
    if not isinstance(volume, dict):
        return None

    volume_info = volume.get("volumeInfo") or {}

    title = (volume_info.get("title") or "").strip()
    if not title:
        return None

    authors = volume_info.get("authors") or []
    if not isinstance(authors, list):
        authors = []
    authors_list = [
        author.strip()
        for author in authors
        if isinstance(author, str) and author.strip()
    ]
    if not authors_list:
        return None

    language = volume_info.get("language")
    if isinstance(language, str):
        language = language.strip() or None
    else:
        language = None
    if language and not language.startswith("en"):
        return None

    subtitle = (volume_info.get("subtitle") or "").strip() or None
    description = (volume_info.get("description") or "").strip() or None
    publisher = (volume_info.get("publisher") or "").strip() or None
    published_date_raw = volume_info.get("publishedDate")
    publication_year = _extract_year(published_date_raw)

    page_count = volume_info.get("pageCount")
    if not isinstance(page_count, int) or page_count <= 0:
        page_count = None

    raw_isbn_13, raw_isbn_10 = _extract_isbns(
        volume_info.get("industryIdentifiers")
    )
    isbn_13 = _strip_isbn(raw_isbn_13) if raw_isbn_13 else None
    isbn_10 = _strip_isbn(raw_isbn_10) if raw_isbn_10 else None
    canonical_isbn_13, _isbn_source = canonicalize_isbn(
        raw_isbn_13, raw_isbn_10
    )

    categories_list = volume_info.get("categories") or []
    if isinstance(categories_list, list):
        categories = "|".join(
            category.strip()
            for category in categories_list
            if isinstance(category, str) and category.strip()
        ) or None
    else:
        categories = None

    return {
        "google_volume_id": volume.get("id"),
        "source_query": query,
        "source_queries": query,
        "source_start_index": start_index,
        "source_result_index": result_index,
        "global_rank": start_index + result_index,
        "fetched_at": fetched_at,
        "dedup_key": "",
        "dedup_strategy": "",
        "duplicate_count": 1,
        "title": title,
        "subtitle": subtitle,
        "author": ", ".join(authors_list),
        "isbn_13": isbn_13,
        "isbn_10": isbn_10,
        "canonical_isbn_13": canonical_isbn_13,
        "description": description,
        "page_count": page_count,
        "publication_year": publication_year,
        "published_date_raw": published_date_raw,
        "publisher": publisher,
        "categories": categories,
        "language": language,
        "cover_url": _normalize_cover_url(volume_info.get("imageLinks")),
        "source": "google_books",
    }


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def collect_pages_for_query(
    query_meta: dict[str, str],
) -> list[dict[str, Any]]:
    """Paginate one query and return the rows extracted from all pages."""
    query = query_meta["q"]
    log.info(
        "Query: q=%r target=%s kind=%s",
        query,
        query_meta.get("target"),
        query_meta.get("kind"),
    )

    seen_volume_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for page_idx in range(MAX_PAGES):
        start_index = page_idx * MAX_RESULTS_PER_PAGE
        envelope = fetch_page(query, start_index)
        response = envelope["response"]
        fetched_at = envelope["fetched_at"]

        items = response.get("items") or []
        if not isinstance(items, list):
            items = []

        if not items:
            log.info("stop: empty page q=%r start=%s", query, start_index)
            break

        new_volume_ids_this_page = 0
        kept_before_page = len(rows)
        for result_idx, volume in enumerate(items):
            volume_id = volume.get("id") if isinstance(volume, dict) else None
            if volume_id is not None and volume_id in seen_volume_ids:
                continue
            if volume_id is not None:
                seen_volume_ids.add(volume_id)
            new_volume_ids_this_page += 1

            row = extract_row(
                volume,
                query=query,
                start_index=start_index,
                result_index=result_idx,
                fetched_at=fetched_at,
            )
            if row is None:
                continue
            rows.append(row)

        kept_this_page = len(rows) - kept_before_page
        log.info(
            "page start=%s: items=%s, new=%s, kept=%s",
            start_index,
            len(items),
            new_volume_ids_this_page,
            kept_this_page,
        )

        if new_volume_ids_this_page == 0:
            log.info("stop: no new volume IDs q=%r start=%s", query, start_index)
            break

    return rows


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
QUERY_ORDER: dict[str, int] = {q["q"]: i for i, q in enumerate(QUERIES)}


def _normalize_text(value: Optional[str]) -> str:
    """Normalize text for conservative fallback grouping."""
    if not value:
        return ""
    normalized = value.lower()
    normalized = re.sub(r"[^\w\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _tiebreaker_score(row: dict[str, Any]) -> tuple:
    """Return a comparable tuple where greater means preferred representative."""
    isbn_13 = row.get("isbn_13")
    canonical = row.get("canonical_isbn_13")
    explicit_isbn = 1 if (isbn_13 is not None and isbn_13 == canonical) else 0
    global_rank = row.get("global_rank", 10**9)
    query_index = QUERY_ORDER.get(row.get("source_query", ""), len(QUERIES))

    return (
        explicit_isbn,
        1 if row.get("description") else 0,
        1 if row.get("cover_url") else 0,
        1 if row.get("page_count") is not None else 0,
        1 if row.get("publication_year") is not None else 0,
        -global_rank,
        -query_index,
    )


def _select_representative(
    group: list[dict[str, Any]],
    *,
    dedup_strategy: str,
    dedup_key: str,
) -> dict[str, Any]:
    """Select the best row from a group and aggregate its provenance."""
    representative = dict(max(group, key=_tiebreaker_score))
    strategy = dedup_strategy
    if dedup_strategy == "isbn_13_explicit":
        if representative.get("isbn_13") != representative.get("canonical_isbn_13"):
            strategy = "isbn_13_from_isbn_10"

    seen_queries: dict[str, None] = {}
    for row in group:
        query_blob = row.get("source_queries") or row.get("source_query") or ""
        for query in query_blob.split("|"):
            query = query.strip()
            if query and query not in seen_queries:
                seen_queries[query] = None

    representative["source_queries"] = "|".join(seen_queries.keys())
    representative["duplicate_count"] = sum(
        int(row.get("duplicate_count") or 1) for row in group
    )
    representative["dedup_key"] = dedup_key
    representative["dedup_strategy"] = strategy
    return representative


def _pass_a_volume_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows that share a Google volume ID."""
    groups: dict[str, list[dict[str, Any]]] = {}
    no_volume_id: list[dict[str, Any]] = []

    for row in rows:
        volume_id = row.get("google_volume_id")
        if volume_id:
            groups.setdefault(volume_id, []).append(row)
        else:
            no_volume_id.append(row)

    result = [
        _select_representative(
            group,
            dedup_strategy="volume_id",
            dedup_key=volume_id,
        )
        for volume_id, group in groups.items()
    ]
    result.extend(no_volume_id)
    return result


def _pass_b_isbn(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows that share a canonical ISBN-13."""
    groups: dict[str, list[dict[str, Any]]] = {}
    no_canonical_isbn: list[dict[str, Any]] = []

    for row in rows:
        canonical_isbn = row.get("canonical_isbn_13")
        if canonical_isbn:
            groups.setdefault(canonical_isbn, []).append(row)
        else:
            no_canonical_isbn.append(row)

    result = [
        _select_representative(
            group,
            dedup_strategy="isbn_13_explicit",
            dedup_key=canonical_isbn,
        )
        for canonical_isbn, group in groups.items()
    ]
    result.extend(no_canonical_isbn)
    return result


def _split_by_year(group: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a title-author group into year-similar sub-groups."""
    with_year = sorted(
        (row for row in group if row.get("publication_year") is not None),
        key=lambda row: row["publication_year"],
    )
    without_year = [
        row for row in group if row.get("publication_year") is None
    ]

    if not with_year:
        return [group]

    years = [row["publication_year"] for row in with_year]
    if max(years) - min(years) <= 5:
        return [group]

    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    last_year: Optional[int] = None
    for row in with_year:
        year = row["publication_year"]
        if last_year is None or (year - last_year) <= 5:
            current.append(row)
        else:
            clusters.append(current)
            current = [row]
        last_year = year
    if current:
        clusters.append(current)

    if without_year:
        largest = max(clusters, key=len)
        largest.extend(without_year)

    return clusters


def _pass_c_title_author(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ISBN-less rows by normalized title and first author."""
    to_process: list[dict[str, Any]] = []
    already_done: list[dict[str, Any]] = []

    for row in rows:
        if row.get("dedup_strategy"):
            already_done.append(row)
        else:
            to_process.append(row)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in to_process:
        normalized_title = _normalize_text(row.get("title"))
        first_author = (row.get("author") or "").split(",", 1)[0]
        normalized_author = _normalize_text(first_author)
        groups.setdefault((normalized_title, normalized_author), []).append(row)

    result = list(already_done)
    for (normalized_title, normalized_author), group in groups.items():
        for sub_group in _split_by_year(group):
            key = f"{normalized_title}|{normalized_author}"
            years = [
                row["publication_year"]
                for row in sub_group
                if row.get("publication_year") is not None
            ]
            if years:
                key += f"|{min(years)}-{max(years)}"
            result.append(
                _select_representative(
                    sub_group,
                    dedup_strategy="title_author",
                    dedup_key=key,
                )
            )

    return result


def deduplicate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the three-pass dedup algorithm to raw rows."""
    log.info("dedup input: %d rows", len(rows))
    after_a = _pass_a_volume_id(rows)
    log.info("after pass A (volume_id): %d rows", len(after_a))
    after_b = _pass_b_isbn(after_a)
    log.info("after pass B (canonical_isbn_13): %d rows", len(after_b))
    after_c = _pass_c_title_author(after_b)
    log.info("after pass C (title_author): %d rows", len(after_c))
    return after_c


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------
CSV_COLUMNS: list[str] = [
    "google_volume_id",
    "source_query",
    "source_queries",
    "source_start_index",
    "source_result_index",
    "global_rank",
    "fetched_at",
    "dedup_key",
    "dedup_strategy",
    "duplicate_count",
    "title",
    "subtitle",
    "author",
    "isbn_13",
    "isbn_10",
    "canonical_isbn_13",
    "description",
    "page_count",
    "publication_year",
    "published_date_raw",
    "publisher",
    "categories",
    "language",
    "cover_url",
    "source",
]


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write deduped rows to a CSV file, atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=CSV_COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            sanitized = {
                column: "" if row.get(column) is None else row.get(column)
                for column in CSV_COLUMNS
            }
            writer.writerow(sanitized)
    os.replace(tmp_path, path)
    log.info("Wrote %d rows to %s", len(rows), path)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main() -> int:
    """Run the collector end-to-end. Returns exit code 0 or 2."""
    log.info("=" * 70)
    log.info(
        "Atlas fetch_books.py - %d queries, max_pages=%d, max_results=%d",
        len(QUERIES),
        MAX_PAGES,
        MAX_RESULTS_PER_PAGE,
    )
    log.info("=" * 70)

    all_rows: list[dict[str, Any]] = []

    for index, query_meta in enumerate(QUERIES, start=1):
        log.info(
            "[%d/%d] starting query: %r",
            index,
            len(QUERIES),
            query_meta["q"],
        )
        try:
            rows = collect_pages_for_query(query_meta)
        except TransientAPIFailure as exc:
            log.error("Aborting due to API failure: %s", exc)
            return 2
        log.info(
            "[%d/%d] %r -> %d raw rows",
            index,
            len(QUERIES),
            query_meta["q"],
            len(rows),
        )
        all_rows.extend(rows)

    log.info("=" * 70)
    log.info("Total raw rows collected: %d", len(all_rows))
    deduped = deduplicate(all_rows)
    log.info("Unique books after dedup: %d", len(deduped))

    write_csv(deduped, OUTPUT_CSV)

    strategy_counts: dict[str, int] = {}
    for row in deduped:
        strategy = row.get("dedup_strategy") or "(none)"
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    log.info("Dedup strategy breakdown:")
    for strategy, count in sorted(strategy_counts.items()):
        log.info("  %s: %d", strategy, count)

    log.info("=" * 70)
    log.info("Done. Output: %s", OUTPUT_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
