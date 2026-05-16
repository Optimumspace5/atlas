"""Enrich the merged corpus with Google Books rating metadata.

The script walks cached Google Books response envelopes, indexes
`averageRating` and `ratingsCount` by `google_volume_id`, and appends or
refreshes `avg_rating` and `ratings_count` columns in `data/corpus_merged_v1.csv`.
No network calls are made.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/google_books/v1")
CORPUS_CSV = Path("data/corpus_merged_v1.csv")
RATING_COLUMNS = ["avg_rating", "ratings_count"]


def _clean_cell(value: Any) -> str:
    """Return a stripped CSV cell string."""
    return "" if value is None else str(value).strip()


def walk_cache_envelopes(cache_dir: Path) -> Iterator[dict[str, Any]]:
    """Yield valid cache envelopes from a Google Books cache directory."""
    if not cache_dir.exists():
        return

    for path in sorted(cache_dir.glob("*.json")):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("Skipping corrupt cache file: %s", path)
            continue
        if isinstance(envelope, dict):
            yield envelope


def extract_ratings_from_volume(
    volume: dict[str, Any],
) -> tuple[Optional[str], Optional[float], Optional[int]]:
    """Extract volume ID, average rating, and rating count from one volume."""
    if not isinstance(volume, dict):
        return None, None, None
    volume_id = volume.get("id")
    if not isinstance(volume_id, str) or not volume_id.strip():
        return None, None, None

    info = volume.get("volumeInfo") or {}
    avg_rating = info.get("averageRating")
    ratings_count = info.get("ratingsCount")

    if not isinstance(avg_rating, (int, float)):
        avg_rating = None
    else:
        avg_rating = float(avg_rating)

    if not isinstance(ratings_count, int):
        ratings_count = None

    return volume_id, avg_rating, ratings_count


def _prefer_rating(
    existing: tuple[Optional[float], Optional[int]],
    incoming: tuple[Optional[float], Optional[int]],
) -> tuple[Optional[float], Optional[int]]:
    """Prefer the tuple with richer rating data."""
    existing_avg, existing_count = existing
    incoming_avg, incoming_count = incoming

    existing_score = (existing_avg is not None) + (existing_count is not None)
    incoming_score = (incoming_avg is not None) + (incoming_count is not None)
    if incoming_score > existing_score:
        return incoming
    if incoming_score == existing_score and (incoming_count or 0) > (existing_count or 0):
        return incoming
    return existing


def build_ratings_index(
    cache_dir: Path,
) -> dict[str, tuple[Optional[float], Optional[int]]]:
    """Build a ratings lookup keyed by Google volume ID."""
    index: dict[str, tuple[Optional[float], Optional[int]]] = {}
    cache_files = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
    log.info("Walking %d cache files in %s", len(cache_files), cache_dir)

    for envelope in walk_cache_envelopes(cache_dir):
        response = envelope.get("response") if isinstance(envelope, dict) else None
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            continue
        for volume in items:
            volume_id, avg_rating, ratings_count = extract_ratings_from_volume(volume)
            if volume_id is None:
                continue
            incoming = (avg_rating, ratings_count)
            if volume_id in index:
                index[volume_id] = _prefer_rating(index[volume_id], incoming)
            else:
                index[volume_id] = incoming

    return index


def enrich_corpus_csv(
    corpus_path: Path,
    ratings_index: dict[str, tuple[Optional[float], Optional[int]]],
) -> tuple[int, int]:
    """Add rating columns to the corpus CSV and return enriched/missing counts."""
    with corpus_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        rows = [{key: _clean_cell(value) for key, value in row.items()} for row in reader]

    for column in RATING_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    enriched = 0
    missing = 0
    for row in rows:
        volume_id = row.get("google_volume_id", "")
        avg_rating, ratings_count = ratings_index.get(volume_id, (None, None))
        row["avg_rating"] = "" if avg_rating is None else f"{avg_rating:g}"
        row["ratings_count"] = "" if ratings_count is None else str(ratings_count)
        if avg_rating is None and ratings_count is None:
            missing += 1
        else:
            enriched += 1

    tmp_path = corpus_path.with_suffix(corpus_path.suffix + ".tmp")
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
    os.replace(tmp_path, corpus_path)

    return enriched, missing


def main() -> int:
    """Run corpus rating enrichment."""
    ratings_index = build_ratings_index(CACHE_DIR)
    log.info("Built ratings index for %d unique volume IDs", len(ratings_index))

    with CORPUS_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        row_count = sum(1 for _ in csv.DictReader(file))
    log.info("Loaded %d corpus rows", row_count)

    enriched, missing = enrich_corpus_csv(CORPUS_CSV, ratings_index)
    log.info("Enriched %d rows with rating data; %d rows have no rating", enriched, missing)
    log.info("Wrote %d rows to %s with rating columns", row_count, CORPUS_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
