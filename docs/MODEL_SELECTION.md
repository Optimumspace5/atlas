# Embedding Model Selection — v1

This document records the choice of sentence embedding model for Atlas v1 and
the rationale behind it.

## Use case

Atlas embeds book metadata (title + author + description) into dense vectors,
which are stored in pgvector and used for:

- Similarity search ("find books like this one")
- Future query → book retrieval (e.g. "I want to learn options pricing")
- Optional richer recommendation signal layered on top of gap-scoring

This is a **retrieval** problem — not classification, not sentiment, not
paraphrase. The model should be optimised for ranking documents by relevance
to a query.

## Candidates compared

| Model | Dim | Size | MTEB avg | Notes |
|---|---|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | ~80 MB | ~56 | Old general-purpose workhorse; fast but weakest |
| `sentence-transformers/all-mpnet-base-v2` | 768 | ~420 MB | ~57.8 | Strong general sentence model; 2× the vector storage |
| `BAAI/bge-small-en-v1.5` | 384 | ~130 MB | ~62 | Retrieval-tuned (v1.5 explicit improvements); modern |

## Decision: `BAAI/bge-small-en-v1.5`

### Why

1. **Retrieval-tuned.** BGE v1.5 was trained for the exact pattern we use —
   embedding documents so similar items rank close. all-mpnet-base-v2 is a
   strong general sentence embedder; BGE small is specifically a retrieval
   model. For our use case, that targeted training pays off.
2. **Best MTEB benchmark** of the three (~62 vs 56 / 57.8).
3. **Same 384 dimensions as MiniLM**, so no extra vector storage or query
   cost vs the weaker baseline. Doubling to MPNet's 768 dim would not be
   worth the ~5-point MTEB delta over MPNet.
4. **Production-proven** — widely used in real RAG systems.

### Trade-offs accepted

- Slightly larger model file (~130 MB vs ~80 MB for MiniLM). Irrelevant in
  our dev container; would matter for serverless cold starts but we don't
  deploy that way.
- Marginally slower inference than MiniLM (still seconds for the whole 468-
  book corpus). Not a real cost.

### Fallback plan

If spot checks reveal BGE produces poor nearest neighbours on our specific
investing/trading corpus, the fallback is `all-mpnet-base-v2`.

**Important caveat:** switching to MPNet means going from 384-dim to 768-dim
vectors. That requires:

1. A new Alembic migration to alter the `book_embeddings.embedding` column
   (or drop + recreate the table — simpler at this scale, since vectors are
   easily regenerated)
2. Re-running `scripts/generate_embeddings.py` to repopulate
3. Rebuilding the IVFFlat index (depends on column dimension)

For 468 books, all three steps take under a minute. The cost is small enough
that we treat model choice as easily reversible — but the dimension is the
load-bearing schema decision, not the model name.

## Embedding document composition

For each book, we embed the concatenation:

{title} {author} {description}

Books without a description fall back to `title + author` alone. This
matches the document text used by the TF-IDF service (`services/tfidf.py`)
so the two recommendation strategies can be compared apples-to-apples.

## Version pinning

The model and its dimension are pinned to v1 of the embedding artifact. A
v2 (e.g. domain fine-tuned model or different architecture) would require a
new migration and a new `MODEL_SELECTION.md` entry, not an edit to this one.



