"""Fetch raw Google Books metadata for the Atlas investing and trading corpus.

This script queries the Google Books API using taxonomy-aligned search terms,
caches each raw API response on disk, and writes a normalized staging CSV for
later review and database loading. The output is `data/corpus_merged_v1.csv`, while
cached API responses are stored under `data/cache/google_books/v1/`. Run from
the repository root with `python scripts/fetch_books.py` after setting
`GOOGLE_BOOKS_API_KEY` in `.env`.
"""

# standard library imports
# Lets Python interact with the operating system.
# In this script, used to read the API key from environment variables
# and safely replace completed temp files with final files.
import os
# Lets the script exit with a specific status code after main() finishes.
import sys
# Lets the script read and write JSON data.
# Used for saving and loading raw Google Books API responses in the cache.
import json
# Lets the script write rows into a CSV file.
# Used to create the final cleaned book dataset.
import csv
# Lets the script create a short fingerprint for each API request.
# Used to make unique cache filenames.
import hashlib
# Lets the script show progress, warnings, and errors while running.
import logging
# Lets the script search and clean text using patterns.
# Used for ISBN cleanup, year extraction, and title/author normalization.
import re
# Lets the script pause before retrying a failed API request.
import time
# Lets the script work with dates, times, and timezones.
# Used to record when data was fetched and to check valid publication years.
from datetime import datetime, timezone
# Lets the script handle file and folder paths cleanly.
# Used for cache folder paths and output CSV paths.
from pathlib import Path
# Provides type hints to make function inputs and outputs easier to understand.
# Optional means a value can be something or None.
# Iterable means something that can be looped over.
# Any means the value can be any type.
from typing import Optional, Iterable, Any

# third-party imports
# Lets the script send web requests to the Google Books API.
import requests
# Lets the script load secret/config values from the .env file.
# In this script, used so the Google Books API key can be stored outside the code.
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Load values from the .env file into the environment.
# This lets the script access secrets like the Google Books API key
# without hardcoding them directly in the code.
load_dotenv()

# Set up logging so the script can show progress, warnings, and errors clearly.
logging.basicConfig(
    # Show normal progress messages and anything more serious.
    level=logging.INFO,
    # Format each log line as: time | level | message.
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    # Show only hour:minute:second for the log timestamp.
    datefmt="%H:%M:%S",
)
# Create a logger for this script.
log = logging.getLogger(__name__)
# Read the Google Books API key from the environment.
API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY")
# Stop the script early if the API key is missing.
if not API_KEY:
    raise RuntimeError(
        "GOOGLE_BOOKS_API_KEY not set. Add to .env at repo root."
    )

# Google Books API URL used to search for book volumes.
API_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

# Folder where raw Google Books API responses are cached.
CACHE_DIR = Path("data/cache/google_books/v1")
# File path where the final cleaned book dataset will be written.
OUTPUT_CSV = Path("data/corpus_merged_v1.csv")

# Number of result pages to fetch for each search query.
MAX_PAGES = 3
# Number of book results requested per API page.
MAX_RESULTS_PER_PAGE = 40

# Default search settings added to every Google Books API request.
REQUEST_DEFAULTS = {
    # Only return books.
    "printType": "books",

    # Only return English-language results.
    "langRestrict": "en",

    # Ask Google to return the most relevant results first.
    "orderBy": "relevance",

    # Use the configured number of results per page.
    "maxResults": MAX_RESULTS_PER_PAGE,
}

# Wait times used before retrying a failed API request.
RETRY_BACKOFF_SECONDS = [1, 4]
# Earliest publication year considered realistic.
PLAUSIBLE_YEAR_MIN = 1450
# Latest publication year considered realistic: current year plus one.
PLAUSIBLE_YEAR_MAX = datetime.now(timezone.utc).year + 1


# ---------------------------------------------------------------------------
# Search queries (locked in COLLECTOR_DESIGN.md)
# ---------------------------------------------------------------------------
# List of Google Books search terms Atlas will use to collect book data.
# Each query has:
# q = the actual search phrase sent to Google Books
# target = the Atlas topic/category this query supports
# kind = whether it is a broad parent query or a more specific gap-filler query
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

# Build a stable identity for one API request.
# This includes the endpoint, search query, page start index, and default settings.
# The API key is excluded so secrets are not stored in the cache identity.
def _canonical_request(query: str, start_index: int) -> dict[str, Any]:
    return {
        "endpoint": API_ENDPOINT,
        "q": query,
        "startIndex": start_index,
        **REQUEST_DEFAULTS,
    }


# Create a short stable fingerprint for a request.
# Used to make cache filenames unique even if request settings change.
def _request_hash(canonical: dict[str, Any]) -> str:
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:8]


# Turn a search query into a filename-friendly string.
# Example: "risk management trading investing" -> "risk_management_trading_investing"
def _query_slug(query: str) -> str:
    return query.lower().replace(" ", "_")


# Build the full cache file path for a specific query and result page.
# This does not read or write the file; it only creates the path.
def _cache_path(query: str, start_index: int) -> Path:
    canonical = _canonical_request(query, start_index)
    request_hash = _request_hash(canonical)
    filename = f"{_query_slug(query)}_{start_index}_{request_hash}.json"
    return CACHE_DIR / filename


# Try to read a cache file from disk.
# Returns the cached data if valid, or None if the file is missing/corrupt.
def _read_cache(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("Ignoring corrupt cache file: %s", path)
        return None


# Safely write a cache file.
# Writes to a temporary file first, then replaces the final file when complete.
def _write_cache_atomic(path: Path, envelope: dict[str, Any]) -> None:
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

# Custom error used when the Google Books API fails after all retry attempts.
class TransientAPIFailure(Exception):
    """Raised when the Google Books API fails after all retries."""


# Send one request to the Google Books API for a specific query page.
# If the request fails because of temporary problems, retry before giving up.
def _fetch_from_api(query: str, start_index: int) -> dict[str, Any]:
    """Fetch a Google Books page, retrying transient failures.

    Raises:
        TransientAPIFailure: when all retries are exhausted.
    """

    # Build the parameters that will be sent to Google Books.
    # Includes the default request settings, search query, page start index,
    # and API key.
    params = {
        **REQUEST_DEFAULTS,
        "q": query,
        "startIndex": start_index,
        "key": API_KEY,
    }

    # Total tries = first attempt + one attempt for each retry delay.
    total_attempts = 1 + len(RETRY_BACKOFF_SECONDS)

    # Store the latest error message so it can be reported if all attempts fail.
    last_error = "unknown error"

    # Try the request up to the allowed number of attempts.
    for attempt in range(total_attempts):
        try:
            # Send the actual HTTP GET request to Google Books.
            response = requests.get(API_ENDPOINT, params=params, timeout=30)

            # HTTP 200 means success, so return the JSON response as Python data.
            if response.status_code == 200:
                return response.json()

            # Save a short preview of the error response for logging/debugging.
            response_preview = response.text[:200].replace("\n", " ")
            last_error = f"HTTP {response.status_code}: {response_preview}"

            # Only retry rate-limit errors and server errors.
            # Other errors are treated as permanent failures.
            if response.status_code != 429 and not 500 <= response.status_code < 600:
                raise TransientAPIFailure(
                    f"Permanent Google Books API failure for q={query!r} "
                    f"start={start_index}: {last_error}"
                )

        # Handle timeout errors.
        except requests.exceptions.Timeout:
            last_error = "Timeout: request timed out"

        # Handle network connection errors.
        except requests.exceptions.ConnectionError:
            last_error = "ConnectionError: connection failed"

        # Handle invalid JSON responses.
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError):
            last_error = "JSONDecodeError: invalid JSON response"

        # Handle other request-related errors.
        except requests.exceptions.RequestException as exc:
            last_error = f"{type(exc).__name__}: request failed"

        # If retries remain, wait before trying again.
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

    # If every attempt failed, raise an error so the main script can stop cleanly.
    raise TransientAPIFailure(
        f"Google Books API failed after {total_attempts} attempts for "
        f"q={query!r} start={start_index}: {last_error}"
    )


# Get one page of Google Books results, using cache first when possible.
# If the cache is missing, fetch from the API and save the response to cache.
def fetch_page(query: str, start_index: int) -> dict[str, Any]:
    """Return the cached/fetched envelope for a Google Books query page.

    On cache miss, fetches from the API and writes the response to disk
    atomically. Raises TransientAPIFailure if the network call ultimately fails.
    """

    # Build the expected cache file path for this query page.
    path = _cache_path(query, start_index)

    # Try to read a saved response from the cache.
    cached = _read_cache(path)

    # If a valid cache file exists, use it instead of calling Google again.
    if cached is not None and "response" in cached and "fetched_at" in cached:
        log.info("cache HIT q=%r start=%s", query, start_index)
        return cached

    # If a cache file exists but is missing required fields, ignore it.
    if cached is not None:
        log.warning("Ignoring cache file missing response/fetched_at: %s", path)

    # No usable cache was found, so fetch fresh data from Google Books.
    log.info("cache MISS q=%r start=%s -> fetching", query, start_index)
    response_json = _fetch_from_api(query, start_index)

    # Wrap the raw API response with metadata before saving it to cache.
    envelope = {
        "cache_version": 1,
        "request": _canonical_request(query, start_index),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status_code": 200,
        "response": response_json,
    }

    # Save the response to cache safely for future runs.
    _write_cache_atomic(path, envelope)

    # Return the newly fetched response.
    return envelope


# ---------------------------------------------------------------------------
# ISBN canonicalization
# ---------------------------------------------------------------------------

# Clean an ISBN by removing spaces, hyphens, and dots.
# Uppercase is used because ISBN-10 can end with X.
def _strip_isbn(raw: str) -> str:
    return re.sub(r"[\s\-\.]", "", raw).upper()


# Check whether a cleaned ISBN-13 is valid using its check digit.
def _is_valid_isbn_13(stripped: str) -> bool:
    if len(stripped) != 13 or not stripped.isdigit():
        return False
    digits = [int(c) for c in stripped]
    weighted_sum = sum(
        d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12])
    )
    check = (10 - (weighted_sum % 10)) % 10
    return check == digits[12]


# Check whether a cleaned ISBN-10 is valid using its check digit.
# The final character can be X, which represents the value 10.
def _is_valid_isbn_10(stripped: str) -> bool:
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


# Convert a valid ISBN-10 into an ISBN-13.
def _isbn_10_to_13(isbn_10: str) -> str:
    body = "978" + isbn_10[:9]
    digits = [int(c) for c in body]
    weighted_sum = sum(
        d * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits)
    )
    check = (10 - (weighted_sum % 10)) % 10
    return body + str(check)


# Return one consistent ISBN-13 for a book when possible.
# Prefer a valid ISBN-13 directly from Google.
# If missing, convert a valid ISBN-10 into ISBN-13.
def canonicalize_isbn(
    raw_isbn_13: Optional[str],
    raw_isbn_10: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
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
# Extract a realistic 4-digit publication year from a raw date string.
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

# Extract the first ISBN-13 and ISBN-10 from Google Books identifiers.
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

# Pick the best available cover image URL and clean it.
# Google Books HTTP cover links are upgraded to HTTPS.
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

# Convert one raw Google Books volume into one cleaned CSV row.
# Returns None when the book is missing required data or is not English.
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

# Fetch all configured result pages for one search query.
# Uses fetch_page(), so each page is loaded from cache first when possible.
def collect_pages_for_query(
    query_meta: dict[str, str],
) -> list[dict[str, Any]]:
    """Paginate one query and return the rows extracted from all pages."""

    # Get the actual Google Books search phrase from the query metadata.
    query = query_meta["q"]

    # Track Google volume IDs already seen within this query to avoid repeats.
    seen_volume_ids: set[str] = set()

    # Store cleaned rows extracted from all fetched pages.
    rows: list[dict[str, Any]] = []

    # Fetch up to MAX_PAGES pages for this query.
    for page_idx in range(MAX_PAGES):
        # Calculate the starting result number for this page.
        start_index = page_idx * MAX_RESULTS_PER_PAGE

        # Get one page of results, using cache first if available.
        envelope = fetch_page(query, start_index)

        # Pull the raw API response and fetch timestamp from the cache/API envelope.
        response = envelope["response"]
        fetched_at = envelope["fetched_at"]

        # Google Books returns book records under the "items" field.
        items = response.get("items") or []
        if not isinstance(items, list):
            items = []

        # Stop if this page has no results.
        if not items:
            break

        # Convert each raw book volume into a cleaned row.
        for result_idx, volume in enumerate(items):
            # Skip repeated Google volume IDs within the same query.
            volume_id = volume.get("id") if isinstance(volume, dict) else None
            if volume_id is not None and volume_id in seen_volume_ids:
                continue
            if volume_id is not None:
                seen_volume_ids.add(volume_id)

            # Extract and clean the fields needed for the CSV.
            row = extract_row(
                volume,
                query=query,
                start_index=start_index,
                result_index=result_idx,
                fetched_at=fetched_at,
            )

            # Skip invalid, incomplete, or non-English books.
            if row is None:
                continue

            rows.append(row)

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
