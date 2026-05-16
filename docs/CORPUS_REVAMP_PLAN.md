# Corpus Revamp Plan

Last updated: 2026-05-16

## Goal And Scope

Revamp `data/corpus_merged_v1.csv` and bootstrap annotations so Atlas ground truth is anchored on canonical books rather than SEO-ranked Google Books search results.

In scope:

- Backfill audit books missing from the merged corpus.
- Import manual audit annotations from `docs/audit_notes.md` into `data/annotations_v1.csv`.
- Enrich the corpus with Google Books rating fields from cached metadata.
- Generate `data/priority_v2.csv` using audit-first and rating-boosted criteria.
- Document the new corpus and annotation contracts.

Out of scope:

- Open Library supplement.
- Renaming existing `annotation_type='manual'` rows to `manual_cli`.
- Rebuilding `data/priority_150_v1.csv`.
- Edition canonicalization beyond conservative Google Books matching.

Expected outcome:

- Corpus grows from 419 to roughly 434 rows.
- Annotation count grows from 23 to roughly 150 rows.
- Corpus columns grow from 25 to 27 with `avg_rating` and `ratings_count`.
- New priority artifact: `data/priority_v2.csv`.

## Phase 1A: Audit Book Backfill

Script: `scripts/backfill_audit_books.py`

Inputs:

- `docs/audit_notes.md`
- `data/corpus_merged_v1.csv`

Outputs:

- `data/corpus_merged_v1.csv`
- `data/cache/google_books/v1/*.json`
- `scripts/needs_manual_review.txt`

Behavior:

- Parse audit headings from `### Book N: Title - Author` or `### Book N: Title -- Author` style lines.
- Skip books already present by fuzzy title match and author last-name match.
- Query Google Books using `intitle:"<title>" inauthor:"<last_name>"`.
- Auto-add only high-confidence English or language-missing matches.
- Rank candidates by title similarity, author match, language score, explicit ISBN-13, ratings count, and publication year.
- Write uncertain matches to `scripts/needs_manual_review.txt` with top candidates.
- Mark appended rows with `source='audit_must_include'`.
- Preserve Google provenance fields such as `google_volume_id`, `source_query`, ISBNs, and cache metadata.

Auto-add thresholds:

- Normalized title similarity must be at least `0.80`.
- Target author last name must appear in the candidate author list.
- Candidate language must be `en`, `en-*`, or missing.

## Phase 1B: Audit Annotation Import

Script: `scripts/import_audit_annotations.py`

Inputs:

- `docs/audit_notes.md`
- `data/corpus_merged_v1.csv`
- `data/annotations_v1.csv`
- `data/taxonomy_v0.1.yaml`

Outputs:

- `data/annotations_v1.csv`
- `scripts/audit_import_conflicts.log`
- `scripts/audit_import_summary.log`

Behavior:

- Parse YAML-style audit annotation blocks.
- Validate every leaf slug against `data/taxonomy_v0.1.yaml`.
- Resolve every audit book to a corpus row.
- Import missing `(book_key, concept_slug)` rows with `annotation_type='manual_audit'`.
- Skip exact existing annotations.
- Log strength conflicts without overwriting existing rows.

Strength mapping:

- `confirmed` -> `1.0`
- `weak` -> `0.5`
- `conditional` -> `0.3`

## Phase 2: Ratings Enrichment

Script: `scripts/enrich_corpus_ratings.py`

Inputs:

- `data/cache/google_books/v1/*.json`
- `data/corpus_merged_v1.csv`

Output:

- `data/corpus_merged_v1.csv`

Behavior:

- Walk cache envelopes and index `averageRating` and `ratingsCount` by Google volume ID.
- Append or refresh `avg_rating` and `ratings_count` columns.
- Leave missing rating fields blank, not zero.
- Re-running the script produces the same enriched corpus.

## Phase 3: Priority V2

Script: `scripts/generate_priority_v2.py`

Inputs:

- `data/corpus_merged_v1.csv`
- `docs/audit_notes.md`
- `data/specialist_whitelist_v1.csv`

Output:

- `data/priority_v2.csv`

Tier rules:

- Tier 0: all audit books from `audit_notes.md`.
- Tier 1: `ratings_count >= 100` and `avg_rating >= 4.0`.
- Tier 2: `ratings_count >= 50` and `avg_rating >= 3.8`.
- Tier 3: `ratings_count >= 10` and `avg_rating >= 4.0`.
- Tier 4: rows listed in `data/specialist_whitelist_v1.csv`.

Ratings are a boost signal for annotation prioritization, not a recommender ranking input.

## Phase 4: Documentation

Update:

- `docs/COLLECTOR_DESIGN.md` for v1.1 columns, source values, and lessons.
- `docs/ANNOTATION_PRIORITIZATION.md` for the v2 priority method.
- `docs/SCHEMA.md` for `annotation_type` values.
- `docs/audit_notes.md` with import status after import.

## Risk Controls

- No duplicate corpus rows on re-run.
- No partial annotation imports when taxonomy validation fails.
- No overwriting existing annotations on conflicts.
- Atomic writes for all CSV outputs.
- Manual review file for low-confidence audit book lookup.
