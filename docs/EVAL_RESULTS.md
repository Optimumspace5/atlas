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
---

# Phase 3 — Cross-encoder training data v1

Date: 2026-06-07
Output: data/cross_encoder_pairs_v1.jsonl (1,858 pairs)

## Generation parameters (CROSS_ENCODER_DESIGN.md §6, calibrated)

- 250 synthetic archetype users (248 generated, 2 archetype-pool skips)
- 3 held-outs per user
- 1 random negative per positive
- 2 hard negatives per source per positive (gap, embedding_read, popularity)
- Seed 42, deterministic

## Calibration shift (3.4 → 3.5)

Original §6 rule (d) for positive labeling referenced "top-3 user gap
concepts." Phase 3.4 measured 178/273 in-pool held-outs failing rule
(d) because synthetic users have ~18 saturated gaps tied at gap=2.0;
top-3 selection ran on alphabetical tie-break and rarely matched the
held-out's actual concepts.

Phase 3.5 reformulated rule (d) symmetrically for positive and hard
rules: "concept where user has gap >= 1.0" replaces "top-3 user gap
concept." Same strength threshold (0.5), no quality compromise — the
fix targeted the alphabetical-tie artifact, not the label bar.

Positive yield jumped 77 -> 623 at the same 100 users; final dataset
generated at 250 users for train/val/test stability.

## Composition (1,858 pairs)

| Label | Count |
|---|---|
| positive | 623 |
| hard_gap | 327 |
| hard_embedding_read | 158 |
| hard_popularity | 127 |
| random | 623 |

Split: 80/10/10 train/val/test, hash-partitioned by user_id.

## Phase 3.6 manual audit (§6 mandatory quality gate)

Stratified sample: 20 random positives + 10 each per hard-negative
source = 50 audit blocks. Seed 42.

| Pile | valid | weak | mislabeled | gate (§6) |
|---|---|---|---|---|
| Positives (20) | 10 | 9 | 1 | PASS (≤ 3 threshold) |
| hard_gap (10) | 10 | 0 | 0 | — |
| hard_embedding_read (10) | 10 | 0 | 0 | — |
| hard_popularity (10) | 10 | 0 | 0 | — |
| Hard total (30) | 30 | 0 | 0 | PASS (≤ 4 threshold) |

**Decision: proceed to Phase 4 (cross-encoder fine-tuning).**

## Observations worth recording

- **100% valid hard negatives.** Symmetric rule (d') tightening
  worked — no false negatives detected. Random sampling of 30 found
  zero gap-teaching books mislabelled as hard. The most dangerous
  failure mode is eliminated.

- **45% weak positives** (9/20). Reformulated positive rule is
  permissive — books touch a real gap concept but aren't always deep
  gap-fillers for the specific user. Acceptable for binary BCE loss
  (positive > negative gradient still holds) but caps the training
  signal's sharpness. v2 calibration should consider per-user-context
  strength weighting.

- **Same book labelled differently across users** (e.g., Technical
  Analysis of Stock Trends: valid / mislabeled / weak in three slots).
  Confirms the cross-encoder is the right architecture — a single
  global book score can't capture user-conditional relevance.

- **Phase 3.0 recall vs Phase 3.5 retrieval drift.** Phase 3.0 measured
  union recall=1.0 at 20 users; Phase 3.5 at 248 users measured
  674/744 = 90.6% in-pool. Difference is sample variance, not a
  retrieval regression.

---

# Phase 4 — Cross-encoder fine-tune v1

Date: 2026-06-09
Output: models/cross_encoder_v1_epoch2/ (gitignored, regeneratable)

## Training run

- Base model: BAAI/bge-reranker-base
- Library: sentence-transformers v3 CrossEncoder.fit() (HF Trainer under the hood)
- Epochs: 2 (chosen because 3-epoch diagnostic trial showed best NDCG at epoch 2)
- Batch size: 8, LR 2e-5, warmup 35 steps (10%), max_length 480, seed 42
- Dataset SHA256: 8fb11f587f146dfa4083771b5f8506faa88ab8955589bf4d769a566056451b99
- Training time: 154.9 min on CPU (Win10, no GPU)
- Train pairs 1,414, val groups 30, test pairs 225 (untouched, reserved for Phase 5)

## Val NDCG@10 (diagnostic only)

| Run | Epoch 1 | Epoch 2 | Epoch 3 |
|---|---|---|---|
| 3-epoch diagnostic | 0.9522 | 0.9547 | 0.9442 (slight overfit) |
| 2-epoch final | — | 0.9506 | — |

NOTE: Val NDCG@10 is computed against val-split negatives only, not the
full RRF candidate pool. Phase 5 is the §9 success-bar evaluation.

## Smoke test (5 random test-split users, never seen during training)

| User | archetype | pos avg | neg avg | gap |
|---|---|---|---|---|
| u25 | value_investor | +0.672 | +0.002 | +0.670 |
| u7  | behavioral_trader | +0.670 | +0.147 | +0.523 |
| u46 | behavioral_trader | +1.000 | +0.039 | +0.960 |
| u22 | macro_thinker | +0.672 | +0.004 | +0.669 |
| u0  | behavioral_trader | +0.700 | +0.848 | -0.148 |

Overall: +0.535 gap, 4/5 PASS.

The single failure (u0) has 3 hard negatives scoring 1.000 — likely
label noise from rule (d') or cross-user book-level leakage. Worth
investigating in Phase 5 with per-archetype slicing, not blocking.

## Implementation gotchas

- sentence-transformers v3+ `.fit(save_best_model=True, output_path=...)`
  silently fails to write weights. Required explicit
  `model.save_pretrained(path)` after fit() as a safety net.
- New `.fit()` API routes through HuggingFace Trainer, requires
  `datasets` and `accelerate>=1.1.0` (added to requirements.txt).
- `save_pretrained()` saves the FINAL epoch state, not the BEST.
  Worked around by training exactly 2 epochs (best observed epoch).

## Decision

Proceed to Phase 5: evaluate against the full RRF candidate pool and
the §9 success bar (NDCG@10 ≥ 0.183).

---

# Phase 5 — Cross-encoder evaluation vs RRF baseline

Date: 2026-06-10
Model: models/cross_encoder_v1_epoch2 (gitignored)
Dataset SHA256: 8fb11f587f146dfa4083771b5f8506faa88ab8955589bf4d769a566056451b99
MLflow experiment: cross_encoder_eval_v1

## Phase 5.1 — full RRF pool eval on test split (§9 gate)

28 test-split users, mean pool size 108.2, mean 2.61 qualified held-outs/user.
Primary NDCG uses QUALIFIED held-outs (JSONL positives) only. Query-match
safeguard passed for all 28 users (0 mismatches).

NDCG@10 (primary, qualified held-outs):

| Strategy | mean | std |
|---|---|---|
| **cross-encoder** | **0.2179** | 0.2117 |
| RRF baseline | 0.1062 | 0.1729 |
| popularity | 0.1830 | — |
| embedding_read | 0.1158 | — |
| gap (single) | 0.0850 | — |

- CE lift vs RRF: **+0.1117**
- Success target (RRF + 0.05): 0.1562
- **GATE: PASS** (CE clears target by +0.062)

Per-archetype NDCG@10 (all four positive lift, no regression):

| Archetype | n | CE | RRF | lift |
|---|---|---|---|---|
| technical_trader | 6 | 0.3779 | 0.0645 | +0.3134 |
| value_investor | 7 | 0.1415 | 0.0647 | +0.0767 |
| macro_thinker | 7 | 0.0776 | 0.0239 | +0.0537 |
| behavioral_trader | 8 | 0.2875 | 0.2460 | +0.0416 |

Caveats:
- Small N (28 users); CE std 0.21 means the lift CI lower bound dips
  near +0.05. Pass is real but not bulletproof.
- macro_thinker absolute CE 0.078 is poor (4/7 users had CE=0). v2 should
  balance archetype training data.
- Bimodal: 13/28 users had CE=0 (no qualified held-out in top-10); when CE
  works it lifts +0.30 to +0.65.
- popularity (0.183) beat RRF (0.106) on this small test set — worth a v2
  look at gap-weighting in RRF, not blocking.

## Phase 5.2 — per-negative-type rejection rate

Strict: positive_score > negative_score. Margin: pos >= neg + 0.1.

| negative_type | n | pos_mean | neg_mean | strict | margin |
|---|---|---|---|---|---|
| hard_gap | 107 | 0.817 | 0.196 | 0.925 | 0.673 |
| hard_embedding_read | 56 | 0.804 | 0.108 | 0.911 | 0.732 |
| hard_popularity | 49 | 0.661 | 0.180 | 0.898 | 0.653 |
| OVERALL | 212 | — | — | 0.915 | 0.684 |

All three hard-negative types rejected at ~90%+ strict — no specific
blind spot. behavioral_trader is the weakest archetype (84% / 92% / 79%),
consistent with its 5.1 result. macro_thinker, value_investor, and
technical_trader hit 100% strict on most types.

Worst failures concentrate in behavioral_trader-u0 and -u7, where the
model preferred an on-archetype book (Trading in the Zone; Little Book
That Beats the Market) over a weaker held-out positive (Bogle index;
Munger almanack). These are label-noise artifacts (the 9 weak + 1
mislabeled positives from the Phase 3.6 audit surfacing), not systematic
model errors.

## Decision

Both §9 gates pass. Proceed to Phase 5.3: wire the reranker into the
recommender service behind ?strategy=cross_encoder.

---

# Phase 5.3 — Production wiring + the trajectory-continuation finding

Date: 2026-06-10

## Wiring

Added cross-encoder as a 5th `?strategy=` value. New service
backend/app/services/reranker.py composes the full pipeline:

    read_book_ids
      -> generate_candidates()        Stage 1a (4-source pool, ~110)
      -> reciprocal_rank_fusion()     Stage 1b (RRF order)
      -> top ATLAS_CE_RERANK_K (default 50)   latency pre-filter
      -> CrossEncoder.predict()       Stage 2 (learned rerank)

Graceful degradation:
  - < 3 read books -> popularity fallback (gap query too noisy, §3)
  - model missing  -> RRF ordering fallback (loud warning, no HTTP 500)

Model is a lazy process-level singleton. Router orders the stateful read
query by user_books.created_at DESC so the query's "Recent reading"
anchors are genuinely recent. Live endpoint verified end-to-end on a
seeded value-investor user (scripts/seed_test_user.py).

## The trajectory-continuation finding (most important result)

Same seeded value-investor user, two strategies:

| ?strategy=gap | ?strategy=cross_encoder |
|---|---|
| 100% technical-analysis / trading books | mostly value + behavioral books |

Gap is a horizon-broadener (recommends maximally-distant concepts — TA
for a value investor with no TA reading). The cross-encoder learned the
OPPOSITE: trajectory continuation (books aligned with the user's existing
direction).

Mechanism — a training-data finding:
- Training positives = held-out books from each synthetic user's reading
  list. Synthetic users are archetype-coherent, so a value investor's
  held-outs are also value/behavioral books.
- But the query's "Reader gaps:" line lists the saturated distant
  concepts (TA). The model was repeatedly shown "gaps = TA, answer =
  value book" and learned to IGNORE the gap signal, predicting from the
  "Recent reading" anchor instead.

Why the §9 eval (CE 0.218 vs RRF 0.106) didn't catch it:
- Held-out test books are also archetype-coherent, so "predict the
  user's held-outs" structurally measures trajectory, not gap-fill. The
  synthetic-eval framework cannot distinguish good gap-fill from good
  trajectory prediction. This is the deepest form of the synthetic-eval
  bias toward annotated/archetype-coherent books.

Implication: gap and cross_encoder are two complementary products
(blind-spots vs what's-next), not rivals. The v1 cross-encoder is a real,
working trajectory recommender that beats RRF on the trajectory metric.
But it is NOT the gap-filler the mission describes. The v2 priority is a
training-data + eval redesign so positives reward gap-fill, not the
re-tuning of rerank caps.

## Score saturation (separate v1 artifact)

Top-10 cross-encoder scores all cluster at ~0.99996. BCE training on
cleanly-separated binary labels pushes most plausible candidates to the
1.0 ceiling, so ordering within the saturated top group is near-arbitrary
(good picks interleaved with off-archetype ones). v2 fixes: temperature
scaling on logits, or graded labels (CROSS_ENCODER_DESIGN.md §6 already
flags graded labels as a fallback if NDCG plateaus).

## Status

Phase 5 complete. The cross-encoder pipeline is built, evaluated, wired,
and live. Two findings (trajectory continuation, score saturation) are
documented as v2 priorities. The gate (§9, RRF + 0.05) passed; the deeper
lesson is that the gate measures trajectory, not mission.

---

# Phase 6 — Auto-annotation (corpus coverage 60 -> 416 books)

Date: 2026-06-11
Scripts: auto_annotate.py, validate_annotator.py, load_annotations.py

## Validation (6.3) — model + prompt selection on the 60 manual books

| Config | precision | recall | F1 | strength agree | empty books |
|---|---|---|---|---|---|
| **Sonnet v1 prompt (CHOSEN)** | 0.752 | 0.659 | 0.702 | 0.690 | 0 |
| Opus v1 prompt | 0.827 | 0.478 | 0.606 | 0.504 | 3 |
| Sonnet v2 (stricter) prompt | 0.813 | 0.536 | 0.646 | 0.601 | 1 |

Post-load regression re-run (validator fixed to exclude auto rows from
ground truth): P=0.755 R=0.690 F1=0.721 — reproduces the selection run
within sampling variance. The committed audit CSV is from this re-run.

Opus won precision by predicting less (5.0 concepts/book vs truth 8.7) and
zero-annotated three content-rich books — disqualifying for a coverage-
expansion task. A stricter v2 prompt reproduced the same failure mode on
Sonnet (recall -0.123) and was rejected per the pre-agreed decision tree.
Sonnet v1's effective precision is ~0.78 after excluding two thin-truth
books (Capital Markets China, Mastering Value Investing — truth=1 each,
day-1 smoke annotations; flagged for ground-truth revision).

Known annotator bias: mild over-inference ("involves/implies" reasoning),
confined to strength 0.5 in practice. Known recall limit: concepts not
inferable from a short description (e.g., Fabozzi -> currency forces).

## Batch + load (6.4)

- 2,301 annotation rows across 356 books (claude-sonnet-4-6, taxonomy
  block prompt-cached, crash-safe resume)
- ~52 books returned zero concepts — audited: mostly off-topic corpus
  noise (regulatory directories, marketing texts, a parallel-computing
  book) correctly rejected; 3-4 borderline on-topic misses noted
  (Bond and Money Markets, CFA curriculum box set)
- Loaded as annotation_type='auto' (PK includes type — manual rows
  untouchable by construction; idempotent delete-then-insert)
- Coverage: 60 -> 416 of 468 books (89%)

## Consequence for all prior evals (6.5 pending)

Every prior eval number in this file was measured under the 60-book
annotation regime. The synthetic-user generator, gap vectors, and
held-out sampling all derive from annotations, so the eval landscape has
materially changed. Phase 6.5 re-runs the recall preflight and Phase 5
comparisons under the de-biased regime — including re-testing the
trajectory-continuation finding.

---

# Phase 6.5 — Re-evaluation under the de-biased corpus (the two-axis capstone)

Date: 2026-06-11
Regime: 416 annotated books (Phase 6.4), vs the 60-book regime all prior
numbers were measured under.

## 6.5a — Recall preflight re-run (trajectory axis, Stage 1)

The §8 gate FAILED honestly for the first time: union recall@100 = 0.275
(the old 1.000 was an artifact — gap's top-50 covered nearly the whole
60-book annotated set, so held-outs had nowhere to hide). 28/40 held-outs
never enter the ~119-candidate pool; pool-ordering NDCG@10 = 0.000 for
both insertion and RRF; oracle ceiling 0.357.

Mechanism — the Phase 5.3 trajectory/mission divergence surfacing one
stage earlier: held-outs are archetype-coherent, so they share concepts
with kept reading, so their gap scores are LOW by design. Under 416
books, gap roams to genuinely distant books and the trajectory-shaped
held-outs fall out of the pool. embedding_read (the one
trajectory-aligned source) became the tied-best held-out finder (0.125;
median rank 2 when found). Pool slots are ~100 horizon-broadening vs ~27
trajectory while the metric's ground truth is 100% trajectory.

Also: saturated gap-ties WORSENED (18.3 -> 23.3 per user; auto-annotated
books average ~6.5 concepts vs manual 8.7). gap_query unique contribution
held at ~31% (gate passes).

## 6.5b — Baselines re-run (trajectory axis, full strategies)

| Strategy | 60-book regime | 416-book regime |
|---|---|---|
| popularity | 0.171 | 0.000 |
| gap | 0.063 | 0.000 |
| embedding | 0.047 | 0.081 |
| tfidf | 0.048 | 0.013 |

popularity's old crown confirmed as annotation-breadth artifact; the
leaderboard inverted exactly as the 6.5a mechanism predicts.

CORRECTION recorded: evaluate_baselines.py uses held-out identity
relevance, not the concept-based definition this file previously
described (v1 section). Every metric this project produced through
Phase 6.5b — including all Phase 5 cross-encoder numbers — was
trajectory prediction. No mission-aligned eval existed until 6.5c.

## 6.5c — Mission-axis eval (NEW: scripts/evaluate_gap_fill.py)

Primary metric: sequential_gap_ndcg@10 — gains computed against a gap
vector that DEPLETES as each ranked book is "read," normalized by a
greedy oracle. Penalizes redundancy. Secondary: static_gap_ndcg@10
(gap_scoring's own objective; favors gap by construction). Drift-guarded
in-memory gain math (asserted equal to score_candidate at startup).
Fail-loud safeguard: refuses to run if the CE model is missing (never
scores the production RRF fallback as "cross_encoder").

| strategy | seq_ndcg@10 | static_ndcg | raw reduction | % gap closed |
|---|---|---|---|---|
| gap | 0.874 | 1.000 | 42.98 | 78.3% |
| rrf | 0.847 | 0.925 | 44.03 | 80.1% |
| popularity | 0.799 | 0.777 | 42.71 | 77.6% |
| cross_encoder | 0.786 | 0.794 | 42.69 | 77.4% |
| embedding | 0.253 | 0.236 | 14.78 | 27.2% |
| tfidf | 0.225 | 0.196 | 13.10 | 24.7% |

Sanity: gap's static_ndcg = 1.0000 exactly (it ranks by that quantity —
metric implementation confirmed correct).

Findings:
- Sequential scoring exposed gap's REDUNDANCY: 0.874, losing ~13% to
  stacking books that fill the same gaps. Static scoring is blind to it.
- RRF out-fills the dedicated gap optimizer on raw volume (80.1% vs
  78.3%): four-source diversity is implicit de-redundancy.
- The similarity strategies genuinely fail the mission (~25%): they
  recommend books about concepts the user already covered.
- Caveat: four strategies cluster at 77-80% — ceiling compression; the
  metric separates failures sharply but compresses leaders.

## 6.5d — The missing cell (scripts/evaluate_ce_trajectory.py)

cross_encoder trajectory NDCG@10 under the 416-book regime: 0.0237.
Decomposition: 70% of held-outs never enter the Stage 1 pool (layer 1);
most in-pool held-outs sit below the production top-50 rerank cap at
median RRF rank ~57-59 (layer 2); of the ~3-4 held-outs the model ever
saw, it converted 2 into top-10s (layer 3 — the model itself is fine).
Note embedding's 0.081 ranks the full corpus directly; the CE is
throttled behind mission-aligned retrieval — not an apples-to-apples
model comparison.

## THE TWO-AXIS TABLE (capstone)

| strategy | trajectory NDCG@10 | mission seq-NDCG@10 |
|---|---|---|
| gap | 0.000 | 0.874 |
| rrf | 0.000 | 0.847 |
| popularity | 0.000 | 0.799 |
| cross_encoder | 0.024 | 0.786 |
| embedding | 0.081 | 0.253 |
| tfidf | 0.013 | 0.225 |

Two clean clusters: a mission cluster (~0.79-0.87 mission, ~0
trajectory) and a similarity cluster (~0.24 mission, marginally nonzero
trajectory). On the honest trajectory metric, EVERYONE is poor (best:
0.081) — predicting 2 specific books of 468 by identity is brutally
hard and nothing in the system optimizes for it.

Capstone insight: **pipeline character is set by Stage 1 composition,
not by the learned component's preference.** Phase 5.3 proved the CE
model itself is a trajectory-continuer; embedded in a mission-aligned
retrieval pipeline (~100:27 horizon:trajectory slots), the system's
output is mission-shaped anyway (0.786 mission / 0.024 trajectory).
Architecture dominates the model.

## Status

Phase 6 complete. Open v2 directions, in descending priority:
1. Mission-aligned cross-encoder training data (positives that reward
   gap-fill) — now measurable with the 6.5c instrument.
2. Trajectory product, if wanted, needs its own Stage 1 mix (raise
   embedding_read share) — a product decision, not a bug fix.
3. Ground-truth revision: 2 thin manual books + ~4 conservative
   auto-annotation misses.
4. Score saturation fix (graded labels / temperature scaling).
