# Annotation Prioritization (v1)

The Atlas v1 corpus contains 419 unique books in `data/corpus_merged_v1.csv`.
Of these, 150 are flagged for first-pass annotation in
`data/priority_150_v1.csv`.

## Selection Criterion

The 150 books are those for which the annotator has legitimate access to
the full text (owned copies, NUS library e-book access, or other licensed
sources). The remaining 269 books do not have confirmed full-text access
and will be handled separately.

## Rationale

Annotation quality matters more than annotation coverage for v1. Reading
the actual text — at least table of contents and key chapters — produces
substantially more reliable strength judgments than annotating from
descriptions alone (per `ANNOTATION_GUIDELINES.md` §3 and §5).

Restricting first-pass annotation to legally-accessible books means:

- Every priority-150 annotation can use real content as evidence.
- The annotator avoids any reliance on pirated material.
- The long-tail 269 books remain in the candidate pool with unknown
  coverage and may receive metadata-only `0.3 conditional` annotations
  later, or be excluded from the v1 evaluation set.

## Files

- `data/corpus_merged_v1.csv` — full 419-book corpus (Week 1 deliverable)
- `data/priority_150_v1.csv` — 150-book annotation priority subset
- `data/annotations_v1.csv` — manual annotations against the priority list

## Open Questions

- **[v1.x]** Should the 269 non-priority books receive metadata-only `0.3`
  annotations before v1 evaluation, or be excluded entirely?
- **[v2]** Should the priority list be expanded as new legal access is
  obtained (e.g., new library availability, purchases)?
