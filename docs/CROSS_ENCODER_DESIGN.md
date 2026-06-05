# Cross-Encoder Reranker — Design

Status: planned for Week 6. This document defines the schema, training
pair strategy, and evaluation methodology so implementation can be
mechanical rather than improvised.

This is a v2 of the design. v1 (pre-2026-06-05) used embedding-only
Stage 1 retrieval, which was directionally wrong for Atlas's gap-fill
mission. The v2 design uses hybrid retrieval with four sources and
introduces source-typed training negatives with explicit ambiguous-skip
rules to prevent false-negative poisoning.

## 1. Why a cross-encoder

Atlas ranks books to fill the user's knowledge gaps. The four existing
strategies (gap, popularity, tfidf, embedding) each capture one signal
but each has known weaknesses:

- gap scoring is annotation-bound (only 60 of 468 books labeled)
- embedding similarity is mission-orthogonal (surfaces "more of what
  the user read," not what fills their gaps)
- tfidf has the same orientation problem as embedding
- popularity is gap-blind by construction

No single strategy combines "gap-aware" with "semantic breadth" with
"focused fit." A cross-encoder is the tool that can — it takes a
(user_state, candidate_book) pair as one joint input and produces a
single relevance score conditioned on both sides. Joint encoding lets
it model signals like "this book is exactly the right depth for this
user's top gap" that no single-signal strategy expresses.

### The two-stage architecture

The cross-encoder is the SECOND stage. Stage 1 is HYBRID candidate
generation — a union of four sources (gap scoring, gap-query embedding,
read-similarity embedding, popularity) that produces ~100-130 dedupli-
cated candidates per query. Stage 2 reranks those.

    Stage 1 (hybrid retrieval):    ~100-130 candidates from 4 sources
    Stage 2 (cross-encoder):       rerank to a final top-10

For Atlas's 468-book corpus we could cross-encode every book per
request in ~1 second. The two-stage pattern is a deliberate choice
anyway:

1. SCALE: as the corpus grows past ~5,000 books, single-stage cross-
   encoding becomes infeasible. The architecture future-proofs.
2. DIAGNOSTIC: each Stage 1 source produces its own near-misses,
   yielding source-typed hard negatives the reranker needs to learn
   against. Source attribution is impossible with a monolithic
   retriever.
3. MISSION ALIGNMENT: a multi-source Stage 1 lets us tune what
   fraction of candidates come from gap-aware vs similarity-aware
   sources, controlling the recommender's character through retrieval
   mix rather than ranker tuning.

## 2. Base model

Choice: BAAI/bge-reranker-base (278M params, ~150MB).

We FINE-TUNE the pre-trained checkpoint on Atlas (query, candidate,
label) training pairs. We are not training from scratch — that would
require millions of pairs and significant compute. Fine-tuning adapts
a model that already understands English retrieval semantics to
Atlas's specific gap-fill judgment task, with the ~2,000-2,500 pairs
we generate.

Reasons:

- VENDOR ALIGNMENT: BAAI publishes both the bi-encoder we already use
  (bge-small-en-v1.5) and this cross-encoder. Tokenization conventions,
  documentation, evaluation methodology, and ecosystem tooling are
  consistent across both. Note: the two models share a vendor and
  brand, not an architecture — bi-encoders produce vectors, cross-
  encoders produce scores; their training procedures differ.

- BENCHMARK QUALITY: top-tier on BEIR and MS MARCO reranking
  leaderboards. Currently the strongest open-weight cross-encoder in
  its size class.

- DEPLOYABLE SIZE: 278M params runs on CPU at acceptable latency for
  our corpus. On a modern laptop CPU: ~10-20ms per (query, candidate)
  pair unoptimized; reranking 130 candidates ≈ 1.5-2.5s. With batched
  inference and optional quantization, sub-second is realistic.
  Latency budget at serving time: rerank 130 candidates in under
  500ms.

Alternatives considered:

- cross-encoder/ms-marco-MiniLM-L-6-v2 (22M params). 6-12x smaller and
  proportionally faster. Lower quality (~3-5 points behind
  bge-reranker-base on standard benchmarks). FALLBACK if measured
  inference time exceeds the 500ms budget after batching +
  quantization. Switching cost: re-run training, swap the model
  identifier in the serving layer.

- BAAI/bge-reranker-large (560M params). 1.3GB. Overkill for 468-book
  reranking. Reconsider when the corpus exceeds ~10,000 books or when
  measured cross-encoder quality plateaus on bge-reranker-base.

## 3. Query representation (hybrid)

The cross-encoder's left input is a string representing the user. We
use a HYBRID format combining structured gap signal with anchor book
text:

    "Reader gaps: {top_5_gap_concept_names}. Recent reading: {title_1} by
     {author_1}; {title_2} by {author_2}."

Example:

    "Reader gaps: Margin of Safety; Chart Patterns and Price Action;
     Risk-Reward and Expectancy; Stop-Loss and Exit Rules; Overtrading
     and Impulse Control. Recent reading: The Intelligent Investor by
     Benjamin Graham; Thinking, Fast and Slow by Daniel Kahneman."

### Why this format

- PURE GAP SLUGS are URL-safe but tokenizer-hostile.
  "chart_patterns_and_price_action" fragments into 9 noisy tokens;
  "Chart Patterns and Price Action" tokenizes to 5 clean English ones.
  Transformers learn from natural text — use concept.name, not
  concept.slug.
- PURE BOOK TEXT would be long, dominated by descriptions, and would
  give the model no direct gap signal — only what the user has read.
- HYBRID gives the model both signals in ~50-80 tokens.

### Field construction (locked rules)

- **top_5_gap_concept_names:** call get_gap_vector(read_book_ids).
  Sort by gap value descending; break ties by concept.slug
  alphabetical. Filter to gap > 0. Take top 5. Use the concept's
  `name` field (English), not its `slug`. Join with "; ".

- **Recent reading:** fetch up to 2 most-recent entries from the
  user's reading. Sort by user_books.created_at descending; break
  ties by book.id alphabetical. Format as "{title} by {author}".
  Join with "; ".

### Query construction must live in ONE place

Training (generate_training_data.py) and inference (recommender
service) must construct identical queries. To prevent drift, query
construction lives in a single helper:

    backend/app/services/query_builder.py:
        build_user_query(session, read_book_ids) -> str

Both the training pair generator and the production reranker call
this function. NO ad-hoc string assembly anywhere else.

### Cold-start policy

The reranker is trained on queries derived from users with >= 8 read
books. At inference time:

- 0-2 read books: SKIP the cross-encoder. Fall back to popularity.
  The gap vector is too noisy to produce a meaningful query.
- 3-7 read books: invoke the cross-encoder, but with confidence flag
  set; the recommender service may downweight reranker scores or
  display a "more recommendations as you log more books" note in the UI.
- 8+ read books: full reranker pipeline.

### Token budget

- Query: ~50-80 tokens (structured, predictable, never truncated)
- Candidate text: 100-500 tokens (varies)
- Special tokens ([CLS], [SEP], [PAD]): ~5 tokens
- Total cap: 512 tokens

Truncation policy: if total would exceed 480 tokens (leaving 32-token
headroom), truncate the candidate's description from the END only.
Title and author are always preserved.

### Known limitation: top-5 gap cutoff

The model only sees the user's top-5 gaps. If a candidate book fills
gap #7, the model has no signal in the query that this is a relevant
gap. Such books may be unfairly down-ranked. For v1, this is an
accepted trade-off. For v2, consider increasing to top-10 or
appending "(and N more)" as a footer.

## 4. Document representation

The right input — the candidate book — is the same composition used
elsewhere in Atlas:

    "{title} {author} {description}"

Example with description:
    "Technical Analysis of the Financial Markets John J. Murphy
     The successor to Murphy's bestselling Technical Analysis of the
     Futures Markets, this updated edition includes new material on
     candlestick charting..."

Example without description:
    "Beyond Technical Analysis Tushar S. Chande"

### Fallback when description is NULL or empty

Some books have no description (a property of the data, not a bug).
For these books:

    document_text = f"{title} {author}".strip()

No "None", no trailing whitespace, no placeholder. Such books will
naturally underperform at reranking, mirroring how they underperform
at TF-IDF and bi-encoder embedding ranking. Auto-annotation and
description backfill (separate workstream) are the fixes.

### Truncation policy

Combined input (query + document) must fit in 512 tokens. If total
would exceed 480 tokens:

- title and author are ALWAYS preserved
- description is truncated from the END only

Affects ~5-10% of books (long-description tail).

### Why simple space-concatenated format

This composition is used by services/tfidf.py, scripts/generate_
embeddings.py (bi-encoder input), scripts/generate_concept_embeddings.py
(planned), and the cross-encoder candidate text. Format consistency is
intentional — adding structured markers would change the cross-
encoder's input from what bi-encoder and TF-IDF use, conflating model
quality with text format in any A/B comparison.

### Single source of truth

Document rendering lives in:

    backend/app/services/query_builder.py:
        build_candidate_text(book) -> str

## 5. Stage 1: Hybrid Retrieval

### Why hybrid, not single-source

The cross-encoder reranks whatever Stage 1 hands it. Stage 1's recall
upper-bounds the entire system's NDCG — a gap-filling book that
doesn't appear in the candidate pool can never be ranked into the
top-10, no matter how well the reranker is trained.

Atlas's mission is gap-fill. Every single-source retriever has a known
blindspot relative to this mission:

| Retriever | Blindspot |
|---|---|
| gap scoring | annotation-bound (only 60/468 books labeled) |
| read-similarity embedding | surfaces "more of what user read," opposite to gap-fill |
| TF-IDF | same orientation problem as embedding |
| popularity | gap-blind by construction (no user signal) |

NO single retriever's top-K contains a high-recall set of gap-filling
candidates for an arbitrary user. The fix: union of four sources, each
compensating for the others' blindspots.

### The four sources

| Source | top-K | Mission alignment | Annotation requirement |
|---|---|---|---|
| gap_scoring | 50 | Direct (built for gap-fill) | Requires annotated candidates |
| gap_query_embedding | 50 | Direct (queries gap concepts) | None — uses BGE on all books |
| embedding_read | 30 | Orthogonal (similarity, not gap) | None |
| popularity | 30 | Indirect (gap-blind broad coverage) | Requires annotated candidates |

Total before dedup: 160 candidates
Expected pool after dedup: ~100-130 unique candidates

### gap_scoring (top-50)

Uses backend/app/services/gap_scoring.py::rank_candidates with the
user's current read_book_ids over the full corpus. Returns books that
maximize sum-of-min(strength, gap).

Why included: the only DIRECT gap-fill signal. Mission-critical.

Known limitations:
- Annotation-bound. ~60 books participate above zero score.
- v1 scoring uses min() capping which compresses strong contributions.
  Improvements tested as score_candidate_v2 in a separate experiment.

### gap_query_embedding (top-50)  [NEW SOURCE]

Constructs a synthetic "gap query" document by concatenating the
user's top-N gap concepts' names and descriptions. Embeds via BGE
bi-encoder. Returns nearest book embeddings by cosine distance.

Why included: direct gap-fill signal that BYPASSES annotation
sparsity. Every book in book_embeddings (all 468) participates.
Catches books that semantically discuss the user's gap topics even
when no human has annotated them.

Implementation:
    backend/app/services/gap_query_embedding.py (new file)
    Pre-requires:
        - concept_embeddings table (new migration)
        - scripts/generate_concept_embeddings.py (new script)

Known limitations:
- Quality depends on concept.description text quality
- Indirect signal — semantic similarity to gap concepts ≠ guaranteed
  gap-fill. Cross-encoder reranks to judge actual relevance.

### embedding_read (top-30)

Uses backend/app/services/embedding.py::rank_by_embedding. Computes
centroid of read-book embeddings, returns nearest neighbors.

Why included: semantic breadth. Catches thematically adjacent books.
The MINORITY signal — useful, not dominant.

Known limitation: mission-orthogonal. Smaller top-K reflects this.

### popularity (top-30)

Uses backend/app/services/popularity.py::rank_by_popularity.

Why included: catches obvious broadly-covered books that other
sources might miss. Pure fallback. Currently the strongest single
strategy on the gap-fill eval metric (NDCG@10 = 0.171).

Known limitation: gap-blind and user-independent.

### Dedup and source attribution

After fetching from each source, MERGE candidates into a single set.
Each candidate carries metadata about which source(s) surfaced it:

    candidate_metadata = {
        "book_id": uuid,
        "sources": ["gap", "gap_query_embedding"],
        "scores": {
            "gap": 1.8,
            "gap_query_embedding": 0.71,
            "embedding_read": None,
            "popularity_rank": None,
        }
    }

A book retrieved by multiple sources appears ONCE. Its `sources`
list grows. Overlap is healthy.

The cross-encoder itself does NOT see this metadata. It powers:
- hard-negative typing (Section 6)
- post-hoc eval slicing (Section 9)
- debugging

### Pool size and latency budget

| Stage | Count | Notes |
|---|---|---|
| Sources fetched | 160 | Before dedup (50+50+30+30) |
| After dedup | 100-130 | Expected |
| Sent to cross-encoder | 100-130 | All reranked |
| Returned to client | 10 | Top-K after rerank |

| Stage | Target | Worst-case |
|---|---|---|
| Stage 1 (4 retrievers in parallel) | ~50 ms | ~150 ms |
| Stage 2 (cross-encoder, 130 candidates) | ~500 ms | ~2 s |
| Total | ~600 ms | ~2.2 s |

### Tuning knobs

The top-K per source is a deliberate v1 default. Knobs to revisit
after candidate recall evaluation:

- top_K per source: increase for sources contributing unique
  candidates; decrease for sources that mostly duplicate others.
- Number of sources: drop a source if its unique contribution
  (% of pool only this source found) is < 5%.
- Pool size cap: hard ceiling at ~130 unique.

Changes to top-K and source mix do NOT require retraining the
cross-encoder. Only query/document format changes do.

## 6. Training pair generation

### Approach: synthetic users with qualified hold-out + source-typed
###            hard-negative mining + ambiguous-skip rule

For each synthetic archetype user from evaluate_baselines.py:

1. Take their reading history (8-12 books per archetype)
2. Hold out N_HOLDOUT books at random
3. Generate candidate pool via Stage 1 hybrid retrieval (Section 5),
   using the user's KEPT reading as input
4. For each candidate in the pool, apply the LABELING RULES below to
   decide: positive / hard_random / hard_gap / hard_embedding /
   hard_popularity / ambiguous_skip

Only labeled pairs (positive, hard_X) are written to the training
set. Ambiguous pairs are SKIPPED — better fewer high-quality pairs
than noisy ones.

### Positive labeling rules

A candidate is labeled POSITIVE (label=1) iff ALL hold:

(a) The candidate is in the user's held-out set
(b) The candidate has at least one annotation
(c) At least one annotated concept has user_gap >= COVERAGE_TARGET / 2
    (i.e. it actually covers a meaningful gap, not just a topic the
    user is already saturated on)
(d) On at least one TOP-3 user gap concept, the candidate's strength
    is >= 0.5 (filters out books that only conditionally touch top gaps)

If a held-out book fails (b), (c), or (d), it becomes ambiguous_skip,
NOT a negative.

### Negative labeling rules

A candidate is a valid HARD_X negative (label=0, negative_type="hard_X")
iff ALL hold:

(a) The candidate was surfaced by source X in Stage 1 retrieval
(b) The candidate is NOT in the held-out set
(c) The candidate is annotated (we need data to verify weakness)
(d) On the user's TOP-3 gap concepts, the candidate has strength < 0.5
    on each
(e) The candidate's gap_score < (positive's gap_score - MARGIN)
    where MARGIN = 0.3

If a candidate is surfaced by source X but fails (d) or (e), it
becomes ambiguous_skip — NOT a hard_X negative.

For RANDOM negatives, the rules are simpler:
- Sampled from books OUTSIDE all four source top-Ks
- Outside the user's archetype concept affinity
- May be annotated or unannotated
- Always labeled negative_type="random"

### Unannotated candidates

Track is_annotated on every pair. Apply differently per negative type:

- RANDOM negatives: unannotated is fine — random sampling from the
  long tail catches them naturally; genuinely irrelevant.
- HARD_X negatives: ONLY annotated books may be hard_X negatives.
  We need annotation data to verify the "provably weaker" criterion.
  An unannotated book retrieved by a source becomes ambiguous_skip.

Conservative on purpose. False negatives poison training much more
than missing negatives slow convergence.

### Target volume

Per held-out positive, generate:
    1 × random   negative
    2 × hard_gap negatives
    2 × hard_embedding negatives
    2 × hard_popularity negatives
                = 7 negatives per positive (when all conditions met)

Pairs per held-out: 1 positive + 7 negatives = 8 (best case)
                    fewer if any negatives fail criteria → ambiguous_skip

Per user with 3 held-outs: 3 × 8 = 24 pairs (best case)

Synthetic users needed:
    Lower bound (2,000 pairs): ~84 users
    Upper bound (3,500 pairs): ~146 users

Recommend: 100 synthetic users × 3 held-outs = 2,400 pairs best case,
realistically 1,800-2,200 after ambiguous_skip drops.

### Label scheme

Binary 0/1:
- 1 = qualified held-out positive (all four positive rules satisfied)
- 0 = qualified hard-X negative or random negative

Matches BGE-reranker's public fine-tuning recipe (BCE loss). Graded
labels deferred to v2 if NDCG plateaus.

### Quality audit (manual checkpoint)

After generating training pairs, BEFORE training:
1. Sample 20 random POSITIVES. Manually verify gap-fill validity.
2. Sample 20 random HARD_X negatives. Manually verify weakness.
3. If either audit yields > 3 mislabeled out of 20, INVESTIGATE the
   labeling rules before training.

~30 minutes; prevents wasted training compute.

## 7. Output schema

Training pairs serialize to JSONL — one row per pair:

    {
      "user_id": "synthetic-value_investor-user-2",
      "archetype": "value_investor",
      "query": "Reader gaps: ... Recent reading: ...",
      "candidate_book_id": "abc-uuid",
      "candidate_text": "Title Author Description",
      "label": 0 or 1,
      "negative_type": null | "random" | "hard_gap" | "hard_embedding" | "hard_popularity",
      "candidate_sources": ["gap", "gap_query_embedding"],
      "gap_score": 1.8,
      "gap_query_score": 0.71,
      "embedding_score": 0.52,
      "popularity_rank": 47,
      "is_annotated": true,
      "split": "train" | "val" | "test"
    }

### Field semantics

- **user_id**: stable identifier for hash-partitioning the split.
- **archetype**: which archetype generated this user. Used for eval
  slicing (e.g., "does the model perform equally well across
  archetypes?").
- **query** / **candidate_text**: the ONLY two fields the cross-
  encoder sees during training/inference.
- **label**: BCE target.
- **negative_type**: for negatives, which kind. For positives, null.
- **candidate_sources**: ordered list of Stage 1 sources that
  surfaced this candidate. >= 1 entry.
- **gap_score / gap_query_score / embedding_score / popularity_rank**:
  raw scores from each Stage 1 source. Use null if the source did not
  surface this candidate. These are METADATA, not model inputs.
- **is_annotated**: whether the candidate has >=1 row in
  book_concept_annotations.
- **split**: one of train / val / test. Hash-partitioned by user_id
  via SHA-256 → fraction; same user always lands in the same split.

### Split policy

Hash-partition by user_id: 80% train, 10% val, 10% test. Same
user_id always maps to the same split, preventing user-level
information leakage between train and val/test.

Stable across runs because the hash is deterministic.

## 8. Candidate recall preflight gate

Before generating training data, run a preflight check on candidate
recall. If the candidate pool doesn't contain held-out positives at
high recall, the cross-encoder has nothing to rerank against.

### Metric

For each synthetic user with held-out positives, measure:

- **Per-source recall @ K**: of the user's held-out books, what
  fraction appear in source X's top-K? Reported per K ∈ {50, 100, 150}
  per source.

- **Union recall @ K**: of the user's held-out books, what fraction
  appear in the DEDUPED union of all source top-Ks?

- **Per-source unique contribution**: of all candidates in the union,
  what fraction was found ONLY by source X (not by any other source)?

Run on the same 100 synthetic users used for training data
generation, but with held-outs visible to the eval harness.

### Quality gates

MUST PASS BEFORE GENERATING TRAINING DATA:

| Gate | Threshold | Action if failed |
|---|---|---|
| Union recall @ 100 | >= 0.90 | DO NOT TRAIN. Either: (a) increase top-K per source; (b) investigate gap_query_embedding text composition; (c) consider annotation expansion |
| Union recall @ 150 | >= 0.95 | DO NOT TRAIN. Same remediations. |
| Every archetype has at least one source with recall >= 0.30 | required | Indicates which archetype is poorly served; tune that archetype's source mix |
| gap_query_embedding unique contribution | >= 0.05 | If new source contributes < 5% unique, it isn't earning its place; tune or remove |

### Output

Single CSV: per-(user, source) recall@K values. Summary table by
source and archetype. Log to MLflow experiment
`candidate_recall_v1`.

### Implementation

    scripts/evaluate_candidate_recall.py

Independent of training data generation. Can be re-run any time the
candidate generation logic changes.

## 9. Evaluation

### What we evaluate

NDCG@10 on the test split of training data (10% of synthetic users
held back at split time).

### Baselines to compare against

Not just bi-encoder alone. Compare against the full set:

| Baseline | Why include |
|---|---|
| Hybrid retrieval alone (no cross-encoder rerank) | Establishes Stage 1 ceiling |
| Popularity | Current eval winner; must beat this to justify the project |
| Gap scoring v1 | Original mission-aligned strategy |
| Gap scoring v2 (if A/B established) | Better mission-aligned strategy |
| Bi-encoder alone (rank_by_embedding) | What the prior design proposed as Stage 1 |

### Success criterion

The cross-encoder must improve NDCG@10 by >= 0.05 absolute over the
**strongest non-rerank strategy** measured on the test split. If the
strongest baseline is popularity at 0.18, the cross-encoder must hit
>= 0.23. If a baseline is at 0.30, the cross-encoder must hit >= 0.35.

This is a stricter criterion than "improve over bi-encoder" because
bi-encoder isn't actually the most relevant baseline for Atlas.

### Per-negative-type discrimination

Beyond aggregate NDCG, slice the eval by:

- **hard_gap rejection rate**: of hard_gap negatives in the test set,
  what fraction does the cross-encoder rank BELOW positives?
- **hard_embedding rejection rate**: same for hard_embedding.
- **hard_popularity rejection rate**: same for hard_popularity.

These slices reveal whether the cross-encoder learned to reject each
specific failure mode the hybrid retrieval introduces. If hard_
embedding rejection is low, the model is being fooled by semantic
similarity exactly the way Atlas needs it not to be.

### Per-archetype slicing

Report NDCG@10 by archetype (value_investor, technical_trader, etc.).
A model that wins on aggregate but fails for one archetype is a
worse system than the average suggests.

### MLflow

Log to experiment `cross_encoder_eval_v1`. Each test-set evaluation
becomes one run with the strategy/model/config as tags.

## What this document does NOT cover

Implementation specifics deferred to phase-specific execution:

- Tokenizer setup details beyond the 512-token cap
- Training hyperparameters (batch size, learning rate, epochs)
- Inference batching strategy
- Model serving (where the model lives in the FastAPI app)
- Caching layer for reranker scores
- Auto-annotator (separate project, may be prerequisite)

These are mechanical decisions made during implementation. This
document locks down schema and methodology that can't change without
invalidating training data already generated.

## Open questions

- **Synthetic user diversity**: 100 users from 4 archetypes is 25 per
  archetype. Are 4 archetypes enough variation? Consider adding more
  archetypes (e.g., "options trader," "real estate") if eval reveals
  per-archetype gaps.

- **Tokenizer choice**: bge-reranker-base comes with its own
  tokenizer (XLM-Roberta-based). Confirm 512-token cap is sufficient
  when the candidate text is long.

- **Class balance**: with ~7 negatives per positive, ~12.5% of pairs
  are positive. Consider class weighting or balanced batch sampling.

- **MMR diversification in production**: a future enhancement could
  add MMR-style diversity at the final top-10 stage to avoid
  surfacing 10 books on the same concept. Out of scope for v1.
