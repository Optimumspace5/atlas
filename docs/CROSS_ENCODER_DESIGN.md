# Cross-Encoder Reranker — Design

Status: planned for Week 6. This document defines the schema, training pair
strategy, and evaluation methodology so implementation can be mechanical
rather than improvised.

## Why a cross-encoder

Atlas currently uses four ranking strategies, the strongest semantic one
being a BGE-small **bi-encoder**: user reading → mean embedding, books →
embeddings, score = cosine similarity. The bi-encoder is fast (O(1) per
candidate after one embedding lookup) but lossy — it embeds the user and
the book independently, so subtle joint signals (e.g. "this book covers
exactly the topics this user has gaps on, with the right depth") are not
modelled.

A **cross-encoder** takes (query, candidate) as a single joint input and
produces one relevance score per pair. Slower but more accurate.

The standard production pattern is two-stage retrieval:

    Stage 1 (bi-encoder):  retrieve top-50 from 468 books in ~5ms
    Stage 2 (cross-encoder): rerank those 50 in ~200ms total
    Final:                 top-10 returned to user

For Atlas's 468-book corpus we could technically cross-encode every book
per request, but the two-stage pattern is what scales and what the design
should target.

## Base model

**Choice: `BAAI/bge-reranker-base`** (278M params, ~150MB).

Reasons:
- Same family (BGE) as our bi-encoder, so retriever and reranker share
  design lineage and training conventions
- State-of-the-art on standard retrieval reranking benchmarks (BEIR, MS MARCO)
- Manageable size; trains and serves on a single GPU or modern CPU

Alternatives considered:
- `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M params) — much smaller and
  faster but lower quality. Listed as a fallback if bge-reranker-base is
  too slow on CPU for reasonable response time.
- `BAAI/bge-reranker-large` (560M params) — overkill for our corpus size.
  Reconsider at 100k+ books.

## Query representation (hybrid)

The cross-encoder's left input is a string representing the user. We use a
**hybrid format** that combines structured gap signal with anchor book text:

    "Reader gaps: {top_5_gap_concept_slugs}. Recent reading: {title_1} by
     {author_1}; {title_2} by {author_2}."

Example:

    "Reader gaps: margin_of_safety, chart_patterns_and_price_action,
     risk_reward_and_expectancy, stop_loss_and_exit_rules,
     overtrading_and_impulse_control. Recent reading: The Intelligent
     Investor by Benjamin Graham; Thinking, Fast and Slow by
     Daniel Kahneman."

Why hybrid:
- Pure gap slugs are compact but lossy; the model loses semantic richness
- Pure book text would be long and dominated by descriptions; "what's
  missing" is the actual signal the recommender needs
- Hybrid gives the model both signals in ~50-80 tokens, well under the
  512-token cap

Concretely:
- `top_5_gap_concept_slugs`: from `get_gap_vector`, sorted desc by gap,
  filtered to gap > 0, take top 5
- `Recent reading`: 1-2 most recently logged books from `user_books`
  (ordered by `created_at` desc)

## Document representation

The right input — the candidate book — is the same composition used
elsewhere in Atlas:

    "{title} {author} {description}"

Matches `services/tfidf.py` and `scripts/generate_embeddings.py`.

## Training pair generation

### Approach: synthetic users with hold-out + hard-negative mining

For each synthetic archetype user from `evaluate_baselines.py`:

1. Take their reading history (8-12 books per archetype)
2. **Hold out 1-3 books** at random — these become positives
3. **Sample negatives** for each positive:
   - **50% random:** sampled from books outside the user's reading and
     outside their archetype's concept affinity (easy negatives)
   - **50% bi-encoder near-misses:** for each held-out book, run the
     stage-1 BGE retriever using the *non-held-out* portion of reading
     as the query. Take the top-50 results, exclude the held-out book
     itself, sample 1-3 from positions 5-50 (hard negatives the
     reranker needs to learn against)

### Target volume

- **20-50 synthetic users** (matches eval harness scale)
- **3 held-out books × 4 negatives each = 12 pairs per user × 1 positive each = 4 pairs per user**

  Actually: per held-out positive, we generate 4 negatives (2 random + 2
  hard) for a total of 5 pairs per held-out book. With 3 held-outs per
  user and 25 users on average:

      25 users × 3 held-outs × 5 pairs = 375 positives + 1,500 negatives
      = 1,875 pairs

  Bumping users to 50 → ~3,750 pairs.

  **Target range: 2,000-3,500 pairs.** Matches the planning estimate.

### Label scheme

**Binary 0/1:**
- 1 = held-out book (positive)
- 0 = sampled negative (random or hard)

Matches BGE-reranker's public training recipe (BCE loss). Graded labels
deferred to a future iteration if NDCG plateaus.

## Output schema

Training pairs serialise to JSONL — one row per pair:

    {
      "user_id": "synthetic-archetype-3-user-2",
      "archetype": "technical_trader",
      "query": "Reader gaps: chart_patterns_and_price_action, ... Recent reading: ...",
      "candidate_book_id": "abc-uuid-...",
      "candidate_text": "Technical Analysis of the Financial Markets John Murphy ...",
      "label": 1,
      "negative_type": null,
      "split": "train"
    }

Negative rows have `label: 0` and `negative_type` ∈ {"random", "hard"}.
Split is one of {"train", "val", "test"} — 80/10/10 hash-partitioned by
`user_id` so the same user never appears in both train and val (prevents
overfitting on user-specific patterns).

## Evaluation

Same NDCG@10 metric as `evaluate_baselines.py`, applied to the test split:

- Hold out the same way
- Stage 1: BGE retrieves top-50
- Stage 2 (NEW): cross-encoder reranks
- Compare against:
  - Stage 1 alone (just bi-encoder, what we already have)
  - Popularity (the current winner on gap-fill eval)
  - Gap-fill (our flagship)
- Log to MLflow experiment `cross_encoder_eval_v1`

Success criterion: cross-encoder reranking improves NDCG@10 over
bi-encoder alone by **at least 0.05 absolute** on test set. Anything less
indicates the reranker isn't learning useful joint signal — investigate
hard-negative quality or training hyperparameters.

## What this document does NOT cover

Implementation specifics deferred to Week 6:
- Tokenizer setup and max-length policy beyond the 512 cap mention
- Training hyperparameters (batch size, learning rate, epochs)
- Inference batching strategy
- Model serving (where the model lives in the FastAPI app)
- Caching layer for reranker scores

These are mechanical decisions made during implementation; this document
locks down the *schema and methodology* that can't be changed without
invalidating training data already generated.

## Open questions

- **Tokenizer choice:** bge-reranker-base comes with its own tokenizer
  (XLM-Roberta-based). Confirm 512-token cap is sufficient when the
  candidate text is long (some descriptions hit 400+ tokens alone).
- **Split fingerprinting:** stable hash of `user_id` for train/val/test
  assignment, but the synthetic user IDs need to be stable across runs
  for the split to mean anything.
- **Class balance:** with 4 negatives per positive, 80% of training pairs
  are negative. Consider class weighting or balanced batch sampling.
