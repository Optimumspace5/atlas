# Collector Design — Google Books v1

Last updated: 2026-05-15
Script: `fetch_books.py`
Output: `data/corpus_merged_v1.csv` (Week 1 corpus, Google Books only; Open Library supplement deferred to Week 2)
Cache: `data/cache/google_books/v1/`

## 1. Purpose

Build the initial book corpus for Atlas by querying the Google Books API across investing and trading themes, caching raw responses on disk, deduplicating canonically, and writing one CSV row per unique book. The corpus is the substrate for annotation, embedding, gap detection, and recommendation in later stages.

Success target: **100+ unique books** in `corpus_merged_v1.csv`. The 100+ is a floor, not a ceiling — a clean corpus of 300+ is acceptable and preferred over an artificially trimmed set.

## 2. Search Strategy

11 queries total: 8 broad parent-category queries + 3 targeted gap-fillers against under-covered leaves identified in the 17-book audit on 2026-05-12.

### Parent-category queries

| # | Parent slug | Query string |
|---|---|---|
| 1 | `market_foundations` | `financial markets trading investing` |
| 2 | `fundamental_analysis_and_valuation` | `fundamental analysis valuation investing` |
| 3 | `technical_analysis_and_market_structure` | `technical analysis trading markets` |
| 4 | `risk_management` | `risk management trading investing` |
| 5 | `portfolio_construction_and_asset_allocation` | `asset allocation portfolio management` |
| 6 | `trading_psychology_and_behavioral_finance` | `trading psychology behavioral finance` |
| 7 | `macro_cycles_and_economic_context` | `global macro investing economic cycles` |
| 8 | `strategy_systems_and_execution` | `trading systems backtesting strategy` |

### Gap-filler queries

| # | Leaf target | Query string |
|---|---|---|
| 9 | `order_types_and_execution_mechanics` | `market microstructure order execution` |
| 10 | `financial_statement_analysis` | `financial statement analysis investing` |
| 11 | `execution_quality_and_trade_implementation` | `trade execution slippage liquidity` |

### Query construction rules

- 3–4 words per query. Single-word queries return garbage; long queries over-narrow.
- No quotes — fuzzy match across title/description is desired.
- No field operators (`intitle:`, `subject:`) in v1. Recall first, precision via dedup later.
- Future v2 collectors may use field operators for targeted leaf backfill.

## 3. API Key Handling

- Key stored in `.env` at repo root: `GOOGLE_BOOKS_API_KEY=...`
- `.env` is gitignored.
- Key is read at runtime via `os.environ.get("GOOGLE_BOOKS_API_KEY")`.
- Key is excluded from cache filenames, cache file contents, and the cache-key hash.
- Key is restricted to Books API only in Google Cloud Console; application restrictions set to "None" (local dev script).

## 4. Cache Architecture

### Layout

```
data/cache/google_books/v1/{query_slug}_{start_index}_{request_hash}.json
```

- `query_slug`: query string lowercased, spaces → underscores.
- `start_index`: integer pagination offset (`0`, `40`, `80`).
- `request_hash`: short SHA-256 prefix (4–8 chars) of the canonicalized request JSON, excluding the API key.
- Versioned directory (`v1/`) so future envelope schema changes can move to `v2/` without migrations.

### Cache key

Canonicalized request JSON, deterministically serialized (sorted keys, no whitespace). Includes: `endpoint`, `q`, `startIndex`, `maxResults`, `printType`, `langRestrict`, `orderBy`. Excludes: `key`.

### Envelope format

```json
{
  "cache_version": 1,
  "request": {
    "q": "...",
    "startIndex": 40,
    "maxResults": 40,
    "printType": "books",
    "langRestrict": "en",
    "orderBy": "relevance"
  },
  "fetched_at": "2026-05-14T...",
  "status_code": 200,
  "response": { "...raw Google Books JSON..." }
}
```

### Rules

- **Strict cache, no TTL.** File exists → load from disk. To refresh, delete files manually.
- **Cache 2xx responses**, including empty result pages.
- **Do not cache** 4xx (except deliberate cases), 5xx, network errors, timeouts, or invalid JSON. Treating transient errors as valid empty pages is the worst-case failure mode.
- **Atomic writes.** Write to `path.json.tmp`, then `os.replace()` to `path.json`. Prevents corruption on interrupt.
- **No API key** in filename, hash input, or envelope contents.

## 5. Pagination Strategy

### Loop structure

```
for query in queries:
  seen_volume_ids = set()
  for start_index in [0, 40, 80]:    # max_pages = 3, configurable
    if cache hit: load envelope
    else:
      fetch with key, langRestrict=en, printType=books, orderBy=relevance, maxResults=40
      on transient error: retry (1s, 4s) → abort or skip with loud log
      on 2xx: write envelope to cache atomically
    parse page → extract volume IDs
    new_ids = volume_ids - seen_volume_ids
    seen_volume_ids |= volume_ids
    process page rows
    if len(items) == 0          : stop query
    if len(new_ids) == 0        : stop query
```

### Constants

- `max_pages = 3` (configurable, default 3). 3 pages × 40 = 120 results per query × 11 queries = 1,320 raw candidates.
- `maxResults = 40` (API maximum).
- `start_index` sequence: `0, 40, 80`.

### Stop conditions

A query stops paginating when **any** is true:

1. Hard cap reached (`max_pages`).
2. Page returns 0 items.
3. Page returns zero new (previously unseen) volume IDs for this query.

### Error handling

Transient errors (429, 5xx, timeout, invalid JSON):
- Retry twice with backoff (1s, 4s).
- On total failure: log loudly. Default: abort entire script. Acceptable softer mode: skip the page and continue, but log prominently in EOD summary.
- **Never cached as a valid empty page.**

## 6. Field Extraction

### CSV column contract (in order)

```
google_volume_id
source_query
source_queries
source_start_index
source_result_index
global_rank
fetched_at
dedup_key
dedup_strategy
duplicate_count
title
subtitle
author
isbn_13
isbn_10
canonical_isbn_13
description
page_count
publication_year
published_date_raw
publisher
categories
language
cover_url
source
```

### DB-loadable subset (maps to `books` table)

`title, author, canonical_isbn_13 (→ isbn_13), description, page_count, publication_year, cover_url, source`

Remaining columns are staging/provenance/debug — used by dedup and review, dropped by the eventual loader script.

### Required fields

- **Missing title** → skip row.
- **Missing or empty authors** → skip row. Counted in collector summary.

### Normalization rules

| Field | Rule |
|---|---|
| `title` | trim whitespace |
| `subtitle` | kept separate; loader decides display concat |
| `author` | non-empty list required; trim each; join with `", "` |
| `published_date_raw` | store Google's raw value |
| `publication_year` | first 4-digit number in raw; accept only `1450 ≤ year ≤ current_year + 1`; else NULL |
| `isbn_13` | strip non-digits; require exactly 13 digits; validate check digit |
| `isbn_10` | strip non-digits except trailing `X` (uppercase); validate check digit |
| `canonical_isbn_13` | explicit ISBN_13 if valid; else converted from valid ISBN_10; else NULL |
| `description` | trim whitespace; empty string → NULL |
| `page_count` | integer; `≤ 0` → NULL |
| `cover_url` | prefer `thumbnail`, fallback `smallThumbnail`; rewrite `http://` → `https://` when host is Google Books |
| `categories` | pipe-delimited string in CSV; not loaded into `books` table |
| `language` | preserve as-is; if non-`en` and non-null, consider skipping |
| `source` | constant `"google_books"` |
| `source_result_index` | **absolute** position across the query (page 0 → 0–39, page 1 → 40–79) |
| `global_rank` | `source_start_index + source_result_index` |
| `fetched_at` | ISO 8601 timestamp from cache envelope |

### Explicitly discarded for v1

- `averageRating` — popularity contamination risk for a gap-aware recommender. Raw cache retains it for future use.
- `saleInfo`, `accessInfo` — not relevant to corpus.

## 7. Deduplication Algorithm

### Pre-pass: identifier canonicalization

For every row, compute `canonical_isbn_13`:
1. Validate ISBN_13 check digit; if valid, use as-is.
2. Else validate ISBN_10 check digit; convert to ISBN_13 (`978` prefix + first 9 digits + new check digit).
3. Else `canonical_isbn_13 = NULL`.

### Pass A: Google volume ID collapse

Group by `google_volume_id`. Same volume across multiple queries → one row, provenance aggregated.

### Pass B: canonical ISBN-13 grouping

Among remaining rows with non-null `canonical_isbn_13`: group by `canonical_isbn_13`. Within each group, select representative by tiebreaker.

### Pass C: ISBN-less fallback

Among remaining rows with null `canonical_isbn_13`: group by `(normalized_title, normalized_first_author)` where normalization is lowercase + punctuation-strip + whitespace-collapse. **Additional split:** if `publication_year` disagrees by >5 years within a group, keep them separate.

No loose fuzzy matching in v1.

### Representative-row tiebreaker (within any group)

Applied in this order, monotonically eliminating candidates:

1. Row with explicit ISBN_13 beats row with promoted ISBN_10.
2. Has `description`.
3. Has `cover_url`.
4. Has `page_count`.
5. Has `publication_year`.
6. Lower `global_rank`.
7. Earlier query in the 11-query list.

### Provenance aggregation per dedup group

- `source_queries`: union of all queries that surfaced rows in the group (pipe-delimited).
- `duplicate_count`: number of raw rows collapsed into this representative.
- `dedup_key`: the value that grouped this row (`google_volume_id` / `canonical_isbn_13` / `title+author` slug).
- `dedup_strategy`: `volume_id` / `isbn_13_explicit` / `isbn_13_from_isbn_10` / `title_author`.

### Out of scope: edition collapse

Different editions of the same work (different ISBNs, different publishers, different page counts) remain as **separate rows** in `corpus_merged_v1.csv`. Edition canonicalization is a curation problem deferred to a later stage.

## 8. Error & Logging Behavior

### Logging

- One INFO line per query started.
- One INFO line per page fetched (cache hit vs. network).
- One INFO line per stop condition triggered, naming the condition.
- One WARN line per skipped row, with reason (missing title, missing authors, language filter).
- One ERROR line per transient API failure and retry.
- One INFO summary at end: rows fetched, rows skipped (per reason), unique books after dedup, API calls made, quota used.

### Quota safety

- Maximum API calls per full run: 33 (11 queries × 3 pages). With retries: ≤ 99. Well under the 1,000/day Google Books free tier.
- First run after deleting cache: full quota cost. Subsequent runs: 0 calls (strict cache).

## 9. Open Questions

- **[v1.x]** Should retry policy be configurable (e.g., 3 attempts vs. 2)? Currently hard-coded.
- **[v2]** Should v2 collector use `subject:` field operator for under-covered leaves once we know which leaves are weakest?
- **[v2]** Should the collector integrate Open Library as a second source for ISBN-less or pre-2007 books?
- **[depends]** Should `language` fallback skip be enforced or advisory? Currently advisory.
- **[depends]** Should `categories` from Google be used to seed annotation candidates in a model-annotation pipeline?
- **[lesson]** Google Books returns fewer than `maxResults` per page in practice; "short page" cannot be used as a stop signal. The corrected rule above relies only on empty page + zero-new-volume-IDs.
