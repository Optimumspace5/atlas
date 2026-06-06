# Baseline Evaluation Results — v1

Date: 2026-06-03
Experiment: `gap_fill_eval_v1` (MLflow)
Eval definition: NDCG@10 over synthetic archetype users (4 archetypes × 5 = 20 users); a book is "relevant" if it covers concepts on which the user has a non-zero gap.

## Results

| Strategy | mean NDCG@10 | std | n_users |
|---|---|---|---|
| **popularity** | **0.171** | 0.200 | 20 |
| gap | 0.063 | 0.165 | 20 |
| tfidf | 0.048 | 0.123 | 20 |
| embedding | 0.047 | 0.100 | 20 |

## Interpretation

Popularity dominates on this metric because the eval rewards breadth of concept coverage, and broadly-annotated books mechanically intersect more user gaps. Embedding and TF-IDF score lowest — which is the **correct** outcome, because they are similarity strategies that rank "more of what the user already likes," literally the opposite intent of gap-filling. Gap-fill itself underperforms popularity here, indicating the cap-at-remaining-gap logic in `score_candidate` is conservative compared to popularity's pure-breadth approach.

## What this means

- **Embedding and TF-IDF are not gap-fill strategies.** Comparing them on a gap-fill metric understates their value. Their qualitative output (Soros, Taleb, Munger for an investing+behavioral user) is meaningfully different from gap-fill output (TA textbooks).
- **Future eval iteration:** add a second metric — held-out next-book prediction — where similarity strategies should win and gap should lose. Current single-metric eval has built-in bias.
- **For v1 production:** keep gap as the default user-facing strategy, expose all four behind the `?strategy=` switcher so users can pick their question ("fill my gaps" vs "more like what I read").

---

# Phase 1.5 — Stage 1b RRF rank fusion

Date: 2026-06-06
Experiment: `candidate_recall_v1`

Added Reciprocal Rank Fusion as deterministic Stage 1b reorder of the
3-source candidate pool. Weights v1: gap=1.0, popularity=0.7,
embedding_read=0.4. k_constant=60.

| Metric | Insertion order | RRF | Lift |
|---|---|---|---|
| Recall @ 10 | 0.075 | 0.175 | 2.3× |
| Recall @ 20 | 0.250 | 0.425 | 1.7× |
| NDCG @ 10 | 0.063 | 0.132 | 2.1× |
| Median held-out rank in pool | 30 | 25 | -5 positions |

RRF reclaims 7.3% of the available NDCG gap vs the oracle ceiling
(1.000). No ML required.

# Phase 2 — gap_query_embedding 4th source

Date: 2026-06-06
Experiment: `candidate_recall_v1`

Added gap_query_embedding as the 4th Stage 1a source (top-50, BGE
bi-encoder over weighted concept embeddings).

First attempt: weight 1.2 per CROSS_ENCODER_DESIGN.md plan.
    NDCG@10 = 0.087  (regression of -0.045 vs Phase 1.5)

Diagnosis: synthetic users had many concepts tied at the saturated gap
value (gap = 2.0). Top-5 selection by gap-then-alphabetical produced
near-uniform queries across users (mean overlap with user-specific
held-outs = 0.80 / 5). The 1.2 weight gave this weakly-individuated
signal more pull than user-specific gap_scoring.

Remediation: weight 1.2 → 0.4 (matches embedding_read).
    NDCG@10 = 0.133  (restored to Phase 1.5 baseline)

Manual review of 40 candidates (10 per archetype, 4 archetypes;
scripts/inspect_gap_query.py):

| Label | Count | % |
|---|---|---|
| good_gap_candidate | 7 | 17.5% |
| plausible_long_tail | 27 | 67.5% |
| redundant_similar | 1 | 2.5% |
| irrelevant_noise | 4 | 10% |
| unknown | 1 | 2.5% |

85% good/plausible. "Plausible" cases were semantic-adjacency discoveries
(market microstructure for technical_trader, behavioral finance for
value-heavy readers). The synthetic eval can't measure these because
held-outs are restricted to the 60 annotated books.

**Decision:** gap_query_embedding stays at weight 0.4 as a low-weight
discovery source. Unannotated picks become ambiguous_skip per
training-data hard-negative rules — gap_query widens the candidate pool
but does not define hard negatives.
