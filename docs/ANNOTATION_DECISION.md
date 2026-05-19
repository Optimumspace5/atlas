docs/WEEK_2_ANNOTATION_DECISION.md

# Coverage Status After 60 Books
- 0 empty, 0 singleton, 0 sparse
- 8 adequate, 40 well-covered

# Decision: Pause Manual Annotation, Pivot to Build
The v1-floor of 80 books was a depth heuristic. Breadth is achieved
at 60. Marginal value of book #61–80 < marginal value of starting
SQLAlchemy + corpus loader + embedder.

# The 8 Adequate Leaves (Backup Plan)
If during Week 2 build work I encounter natural opportunities to
annotate books that touch these leaves, do so. Otherwise leave them
at adequate for v1.

[List of 8 leaves with their current book count + the niche they sit in]

# Week 2 Build Sequence (Replaces Annotation)
- Mon-Tue: SQLAlchemy models matching 001_initial_schema.sql
- Wed: Corpus loader (CSV → books table)
- Thu: Annotations loader (CSV → book_concept_annotations table)
- Fri: First book embedder (description → pgvector)

# Defer
- Top-up annotation for the 8 adequate leaves
- Specialist whitelist population
- priority_v2.csv investigation (why only Tier 0)
