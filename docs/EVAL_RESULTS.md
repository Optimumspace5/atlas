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
