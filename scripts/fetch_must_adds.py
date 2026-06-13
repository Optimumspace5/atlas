"""Track B1: fetch metadata for the canonical must-add books via Google Books.

Targeted lookups (NOT the corpus sweep in fetch_books.py, which would overwrite
corpus_merged_v1.csv). Reuses fetch_books.extract_row + its field helpers so the
output rows match the corpus/catalog schema exactly. Writes a NEW file
data/must_adds_v1.csv in the curated-catalog schema, tagged KEEP_A /
keep_source=manual_add. Mutates nothing else; merge into the catalog is a
separate, confirmed step.

Prints each chosen match (title / author / year / ISBN / publisher) so the
edition can be eyeballed before merging. Flags any book it couldn't resolve.

Inputs:  Google Books API (GOOGLE_BOOKS_API_KEY in .env)
Output:  data/must_adds_v1.csv

Usage:
    python scripts/fetch_must_adds.py
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Reusing the corpus collector's parsing + the loaded API key (importing
# fetch_books runs load_dotenv() and the API-key guard for us).
from fetch_books import API_KEY, extract_row

API_ENDPOINT = "https://www.googleapis.com/books/v1/volumes"
OUT_CSV = Path("data/must_adds_v1.csv")

# (title, author) — the canonical backbone Clarence wants in the catalog.
MUST_ADDS = [
    ("Thinking in Bets", "Annie Duke"),
    ("Superforecasting", "Philip Tetlock"),
    ("Antifragile", "Nassim Nicholas Taleb"),
    ("The Black Swan", "Nassim Nicholas Taleb"),
    ("The Trading Game", "Gary Stevenson"),
    ("The Dao of Capital", "Mark Spitznagel"),
    ("The New Market Wizards", "Jack Schwager"),
    ("Street Smarts", "Jim Rogers"),
    ("A Complete Guide to Volume Price Analysis", "Anna Coulling"),
]

# Penalise derivative knock-offs that pollute Google Books results.
JUNK_TITLE_TERMS = (
    "summary", "summarized", "workbook", "study guide", "key takeaways",
    "conversation starters", "analysis of", "review of", "by nassim",
)

CATALOG_FIELDS = [
    "corpus_row", "google_volume_id", "title", "subtitle", "author",
    "isbn_13", "canonical_isbn_13", "description", "publisher",
    "publication_year", "categories", "language", "cover_url",
    "quality_tier", "relevance_tier", "keep_source",
    "duplicate_group", "collapsed_count",
]


def norm(t: str) -> str:
    t = (t or "").lower().split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def gb_search(query: str, max_results: int = 10) -> list:
    params = {
        "q": query, "printType": "books", "langRestrict": "en",
        "maxResults": max_results, "orderBy": "relevance", "key": API_KEY,
    }
    r = requests.get(API_ENDPOINT, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("items", []) or []


def score(row: dict, target_title: str, target_author: str) -> float:
    nt, na = norm(row.get("title", "")), (row.get("author", "") or "").lower()
    tt = norm(target_title)
    tw, rw = set(tt.split()), set(nt.split())
    overlap = len(tw & rw) / max(1, len(tw))
    surname = target_author.split()[-1].lower()
    author_ok = surname in na
    s = overlap + (0.5 if author_ok else 0.0)
    s += 0.1 if row.get("description") else 0.0
    s += 0.1 if row.get("canonical_isbn_13") else 0.0
    s += 0.05 if row.get("cover_url") else 0.0
    if any(term in nt for term in JUNK_TITLE_TERMS):
        s -= 1.0
    return s


def best_match(title: str, author: str, now: str):
    query = f'intitle:{title} inauthor:{author}'
    items = gb_search(query)
    best, best_s = None, -1.0
    for i, vol in enumerate(items):
        row = extract_row(vol, query=query, start_index=0, result_index=i, fetched_at=now)
        if row is None:
            continue
        s = score(row, title, author)
        if s > best_s:
            best, best_s = row, s
    return best, best_s


def to_catalog_row(idx: int, row: dict) -> dict:
    return {
        "corpus_row": f"add{idx}",
        "google_volume_id": row.get("google_volume_id", ""),
        "title": row.get("title", ""),
        "subtitle": row.get("subtitle", "") or "",
        "author": row.get("author", ""),
        "isbn_13": row.get("isbn_13", "") or "",
        "canonical_isbn_13": row.get("canonical_isbn_13", "") or "",
        "description": row.get("description", "") or "",
        "publisher": row.get("publisher", "") or "",
        "publication_year": row.get("publication_year", "") or "",
        "categories": row.get("categories", "") or "",
        "language": row.get("language", "") or "",
        "cover_url": row.get("cover_url", "") or "",
        "quality_tier": "KEEP_A",
        "relevance_tier": "high",
        "keep_source": "manual_add",
        "duplicate_group": f"add{idx}",
        "collapsed_count": 1,
    }


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    catalog_rows, unresolved = [], []
    print(f"Resolving {len(MUST_ADDS)} must-add books via Google Books ...\n")
    for idx, (title, author) in enumerate(MUST_ADDS, 1):
        try:
            row, s = best_match(title, author, now)
        except requests.RequestException as e:
            print(f"  [{idx}] {title:<42} -> API ERROR: {e}")
            unresolved.append((title, author, "api_error"))
            continue
        if row is None or s < 0.6:
            print(f"  [{idx}] {title:<42} -> UNRESOLVED (best score {s:.2f})")
            unresolved.append((title, author, f"low_score {s:.2f}"))
            continue
        catalog_rows.append(to_catalog_row(idx, row))
        print(f"  [{idx}] {title:<42} -> \"{row.get('title','')[:38]}\" "
              f"by {row.get('author','')[:22]} | {row.get('publication_year','?')} | "
              f"ISBN {row.get('canonical_isbn_13','-')} | {(row.get('publisher') or '-')[:20]} "
              f"(score {s:.2f})")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        w.writerows(catalog_rows)

    print(f"\nWrote {len(catalog_rows)} resolved rows -> {OUT_CSV}")
    if unresolved:
        print(f"\n{len(unresolved)} UNRESOLVED (verify manually or adjust the query):")
        for t, a, why in unresolved:
            print(f"  - {t} / {a}  ({why})")
    print("\nEyeball the matches above. If an edition is wrong, tell me and I'll "
          "tune the query; otherwise we merge must_adds_v1.csv into the catalog.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
