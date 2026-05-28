# Atlas Corpus Manifest — v1

This document describes the v1 corpus shipped in tag `v0.3.0`. It is the
canonical reference for what the recommender operates over.

## Summary

| Field | Value |
|---|---|
| Corpus version | v1 |
| Books | 468 |
| Concepts | 56 (8 parent categories + 48 leaves) |
| Annotations | 519 |
| Annotated books | 60 of 468 |
| Taxonomy | `data/taxonomy_v1.0.yaml` (frozen 2026-05-19) |
| Annotation source | `data/annotations_v1.csv` |
| Released | tag `v0.2.0` (dataset) / `v0.3.0` (HTTP API) |

## Source breakdown

| Source | Books | Provenance |
|---|---|---|
| `google_books` | 419 | Google Books API search across investing/trading queries |
| `curated_top50` | 34 | Hand-picked canonical titles via the curated_top50 pipeline |
| `audit_must_include` | 15 | Audit-flagged books that the search missed but must be present |
| **Total** | **468** | |

## Artifacts

| File | Purpose |
|---|---|
| `data/corpus_merged_v1.csv` | Raw pre-DB corpus, including dedup metadata (`google_volume_id`, `source_query`, `dedup_strategy`, etc.). Authoritative source for re-loading the DB from scratch. |
| `data/corpus_v1.json` | Lean display view exported from the DB. One record per book with `id, title, author, isbn_13, publication_year, page_count, cover_url, source, description`. Used by the frontend and as a portable snapshot. |
| `data/taxonomy_v1.0.yaml` | Frozen taxonomy — 8 parent categories, each with 5-7 leaf concepts. Annotations target leaves only. |
| `data/annotations_v1.csv` | 519 human-authored annotations across 60 books. 17 columns including `book_key`, `concept_slug`, `strength`, `annotation_type`. |
| `data/annotation_coverage_v1.csv` | Per-leaf coverage report (book count, confirmed/weak/conditional breakdown, coverage_status). Every leaf has ≥3 books and ≥1 confirmed annotation. |

## corpus_v1.json schema

```json
{
  "id": "uuid",
  "title": "string (NOT NULL)",
  "author": "string (NOT NULL)",
  "isbn_13": "string|null (13 chars when present)",
  "publication_year": "integer|null",
  "page_count": "integer|null",
  "cover_url": "string|null",
  "source": "google_books | curated_top50 | audit_must_include",
  "description": "string|null"
}
