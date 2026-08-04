# Atlas — 7-Day Mastery & Interview-Defense Plan

**Goal:** by Day 7 you can explain every meaningful line of the ML core, justify
every design decision, and rebuild the ML-critical parts from a blank file — to
the point a skeptical ML interviewer probing for 20 minutes finds no hole.

**Budget assumed:** 2–3 focused hours/day. The plan is cut to the ML-critical
core; scaffolding is explicitly explain-level (don't burn hours on it).

---

## 0. FRAMING CORRECTION (read first — this is the #1 interview trap)

Your brief called the cross-encoder "the core." **The code says it is *not*
deployed.** `backend/app/routers/recommendations.py:34` — *"cross_encoder is
intentionally excluded (offline research result)."* The served default is the
**RRF hybrid** (`rank_by_hybrid`). Your eval found the fine-tuned CE learned
**trajectory-continuation** (recommend what comes next on your current path),
not the **gap-fill** objective the product is about — so you made a principled
decision **not to ship it**.

**Defend it this way (your 60-second pitch):**
> "Atlas recommends investing/trading books by modeling a reader's *knowledge
> gaps* over a 48-concept taxonomy. The served pipeline is two-stage retrieval:
> four complementary retrievers (gap-fill, two embedding signals, popularity)
> fused with Reciprocal Rank Fusion. I also built a fine-tuned cross-encoder
> reranker (bge-reranker-base) plus a rigorous evaluation suite. The eval
> revealed the cross-encoder optimized trajectory-continuation rather than
> gap-fill — measurably sacrificing the product's core objective — so I kept it
> *out* of production and shipped RRF. The interesting result isn't 'I
> fine-tuned a model,' it's 'I measured it honestly, found it solved the wrong
> objective, and made the call.'"

That story (measurement + judgment + a clean negative result) is **harder to
fake and more senior** than "I trained a reranker." Lead with it.

---

## 1. CODEBASE MAP

**End-to-end serving flow (what runs in prod):**
```
user library (UserBook rows)
  -> get_gap_vector(read_ids)                       gap_scoring.py
  -> generate_candidates(read_ids)                  candidate_generation.py
       4 retrievers, each top-K, deduped into one pool (~110):
         1. gap            (rank_candidates)         gap_scoring.py
         2. gap_query_embedding                      gap_query_embedding.py
         3. embedding_read (rank_by_embedding)       embedding.py
         4. popularity     (rank_by_popularity)      popularity.py
  -> reciprocal_rank_fusion(pool)                    candidate_generation.py
  -> top_k -> API response                           routers/recommendations.py, me.py
  (curated=True filter applied in every retriever)
```

**Offline research flow (built, measured, NOT served):**
```
generate_training_data.py  -> data/cross_encoder_pairs_v1.jsonl
train_cross_encoder.py     -> models/cross_encoder_v1_epoch2  (bge-reranker-base, BCE)
reranker.py (rank_by_cross_encoder)  Stage1 RRF -> cap top-50 -> CE.predict -> sort
evaluate_*.py              -> the verdict: trajectory-continuer, not gap-filler
```

**Component → files:**

| Component | Files | Tier |
|---|---|---|
| Gap-scoring core math | `backend/app/services/gap_scoring.py` | **DEEP** |
| Candidate generation + RRF | `backend/app/services/candidate_generation.py` | **DEEP** |
| Embedding retrievers | `embedding.py`, `gap_query_embedding.py` | medium |
| Popularity / TF-IDF | `popularity.py`, `tfidf.py` | light |
| CE query/doc text | `backend/app/services/query_builder.py` | **DEEP** |
| CE reranker (inference) | `backend/app/services/reranker.py` | **DEEP** |
| CE training data gen | `scripts/generate_training_data.py` | **DEEP** |
| CE training loop | `scripts/train_cross_encoder.py` | **DEEP** |
| Eval — baselines (NDCG, synth users) | `scripts/evaluate_baselines.py` | **DEEP** |
| Eval — mission/gap-fill | `scripts/evaluate_gap_fill.py` | **DEEP** |
| Eval — CE axes | `evaluate_cross_encoder.py`, `evaluate_ce_trajectory.py`, `evaluate_hard_negative_rejection.py`, `evaluate_candidate_recall.py` | medium |
| Embeddings build + pgvector | `scripts/generate_embeddings.py`, `models.py` (Vector(384), IVFFlat) | medium |
| API / auth / DB | `routers/*.py`, `auth.py`, `database.py`, `models.py` | light (explain-only) |
| Frontend | `frontend/**` | trivial (explain-only) |

---

## 1.5 SYSTEM ARCHITECTURE — how it all connects (defend this cold)

### Local dev (`docker-compose.yml` — 3 services, one network)
```
[browser :3000 Next.js] ──HTTP + Bearer JWT──► [backend :8000 FastAPI/uvicorn]
        │                                              │ SQLAlchemy, 1 session/request
        └── login ──► [Supabase Auth ☁]                ▼
                            ▲  JWKS (ES256) ──── [postgres :5432 pgvector] (docker volume)
```
- Services: `postgres` (pgvector/pgvector:pg15, healthcheck), `backend`
  (`build ./backend`, `depends_on: postgres healthy`), `frontend`
  (`build ./frontend`). `docker compose up`.
- **Port split to remember:** the *browser* hits the backend at published
  `localhost:8000`; the *backend* hits the DB at the compose-internal hostname
  `postgres:5432`. Same DB, two names.
- **Auth is cloud even locally** — `supabase-js` talks to the Supabase project;
  there is no local auth server.

### Production (3-tier, all managed)
```
[Vercel ☁ Next.js] ──Bearer JWT──► [Render ☁ FastAPI] ──session pooler──► [Supabase ☁ Postgres + pgvector]
        │                                  ▲
        └──── login ──► [Supabase Auth ☁] ─┘ JWKS verify
```
- **Vercel** = frontend (`NEXT_PUBLIC_API_URL` → Render URL). **Render** = backend
  (**Python buildpack, NOT the Dockerfile** — the Dockerfile pulls torch; prod
  installs `requirements-prod.txt`). **Supabase** = Postgres+pgvector (via
  session-pooler URL) **and** the Auth identity provider.
- The *same* Supabase project is the IdP for local and prod — that's why the
  backend only needs `SUPABASE_URL` to derive its JWKS endpoint.

### One authenticated request, end to end
1. **Browser** (`lib/api.ts authHeaders`): attaches `Authorization: Bearer <token>`
   from the `supabase-js` session to `/me/*` calls.
2. **CORS** (`main.py`): origin checked vs `ALLOWED_ORIGINS` + `allow_origin_regex`
   (`https://atlas-.*\.vercel\.app` — survives Vercel's per-deploy URLs).
3. **`get_current_user`** (`auth.py`): `PyJWKClient` fetches Supabase ES256 keys,
   `jwt.decode` verifies signature + `aud`; `sub` → user UUID; **JIT-provisions** a
   `public.users` row if missing.
4. **`Depends(get_db)`** (`database.py`): one pooled `Session` per request, closed
   in `finally` (no connection leak).
5. **Endpoint** (`me.py`) → service (gap/RRF) → SQLAlchemy → Postgres → Pydantic.

### Design decisions to defend
- **Client-side auth (Pattern A), no SSR/middleware:** simpler; backend is the lone
  source of truth. Trade-off: no server-rendered protected pages.
- **Stateless backend:** every request re-verifies the JWT + JIT-provisions; no
  session store → scale by adding Render instances.
- **pgvector, not a dedicated vector DB:** embeddings sit in Postgres beside the
  relational data — one transactional datastore, simpler ops. Trade-off: won't
  scale to 10M+ vectors like Pinecone/Milvus; 232×384 is trivial, so it's right *here*.
- **Docker-parity gotcha:** local is fully Dockerized; Render runs the buildpack,
  not the Dockerfile (avoids shipping torch to a CPU web service). Know where your
  envs diverge.

---

## 2. CONCEPT INVENTORY (two tiers)

### MUST-KNOW THEORY (explain cold; one-line "why it matters" + your blind spot)

- **Bi-encoder vs cross-encoder.** Bi-encoder embeds query & doc *separately*
  (fast, ANN-searchable, used in Stage-1 retrieval); cross-encoder jointly
  encodes `(query, doc)` with full attention (slower, more accurate, used to
  *rerank* a short list). *Why:* it's the entire reason retrieval is two-stage.
  *Blind spot:* be able to say *why you can't ANN-search a cross-encoder* (no
  precomputable doc vector — the score depends on the pair).
- **Why rerank after retrieval.** Cross-encoder over the whole 232-corpus is too
  slow; retrieve a cheap candidate pool first, rerank only the top-K (`reranker.py`
  caps to `CROSS_ENCODER_RERANK_K=50`). *Why:* latency/quality trade-off.
- **bge-reranker-base + BCE.** `num_labels=1` → a single logit → sigmoid →
  relevance in [0,1]; trained with **binary cross-entropy** because labels are
  binary (positive=1, negative=0). *Blind spot:* know *why BCE, not a ranking
  loss like margin/contrastive* here — and that pairwise/listwise would be the
  alternative (be ready to argue the trade-off).
- **Reciprocal Rank Fusion.** `score(c) = Σ_sources w_s / (k + rank_s(c))`.
  Rank-based (ignores raw score scales, so you can fuse a cosine sim with a gap
  score without normalization); `k` damps the top-rank dominance. *Why:* it's
  your *deployed core*. *Blind spot:* what `k` does and why fusing ranks beats
  fusing scores.
- **The gap model.** coverage = Σ annotation strengths per concept; gap =
  max(0, TARGET − coverage), TARGET=2.0; candidate score = Σ min(strength, gap)
  — *capped* so a strong book can't over-fill a small gap. *Why:* the product's
  whole thesis. *Blind spot:* defend TARGET=2.0 and the min() cap as design
  choices, not magic numbers.
- **Embedding retrieval + ANN.** bge-small-en-v1.5, 384-dim, cosine; pgvector
  **IVFFlat** index (`lists≈√N`), built *after* inserts so centroids cluster on
  real vectors. *Blind spot:* IVFFlat is *approximate* — recall/speed knob
  (`lists`, `probes`); know why the index is built post-load.
- **Negative sampling.** Random negatives (off-archetype, easy) + **hard
  negatives** (surfaced by the retrievers but failing the gap rules — i.e. they
  *look* relevant but aren't gap-fillers). *Why:* hard negatives teach the
  decision boundary; random alone → a model that only rejects obvious junk.
  *Blind spot:* the hard-negative *definition* (rules d′ + e in
  `generate_training_data.py`) is the heart of your label design.
- **Evaluation metrics:** **NDCG@k** (DCG/IDCG, binary or graded relevance),
  **MRR@10** (checkpoint selection), **recall@k** (does the candidate pool even
  *contain* the answer — a Stage-1 ceiling), and your custom **sequential
  gap-NDCG** (gain = Σmin(strength, *current* gap), discounted, normalized vs a
  **greedy oracle**; penalizes redundancy). *Blind spot:* be able to derive
  IDCG and explain why sequential ≠ static gap scoring.
- **Train/val/test hygiene.** Split by **user_id hash** so the *same synthetic
  user never crosses splits* (`assign_split`, `assert_no_split_leakage`). *Why:*
  leakage is the first thing a good interviewer probes.
- **The negative result.** Trajectory-continuation vs gap-fill, and *how you
  measured the difference* (`evaluate_gap_fill.py` vs `evaluate_ce_trajectory.py`).

### MUST-BE-ABLE-TO-CODE-FROM-SCRATCH (blank page, then diff against original)

1. **`get_coverage_vector` / `get_gap_vector` / `score_candidate`** — the gap
   math. Small, pure, and the conceptual core. (`gap_scoring.py:41–134`)
2. **`reciprocal_rank_fusion` / `_rrf_scored`** — the RRF formula + sort.
   (`candidate_generation.py:169–211`)
3. **`ndcg_at_k`** (binary) and the **sequential gap-NDCG + greedy oracle**.
   (`evaluate_baselines.py:215–240`, `evaluate_gap_fill.py:142–187`)
4. **The CE training loop skeleton** — load pairs → `InputExample(texts=[q,doc],
   label)` → `CrossEncoder(base, num_labels=1)` → `DataLoader` → `model.fit(...)`
   with warmup + lr, checkpoint by val MRR. (`train_cross_encoder.py:104–260`)
5. **Hard-negative qualification** — given a pool + gap vector, return the
   candidates that pass rules d′ (no strong annotation on a gap-meaningful
   concept) + e (gap-score margin). (`generate_training_data.py:283–332`)
6. **`build_user_query` / `build_candidate_text`** — the exact strings fed to the
   model, and *why they must be identical at train & inference*. (`query_builder.py`)

**Honestly trivial (explain/modify, don't memorize):** FastAPI routes, Pydantic
schemas, SQLAlchemy models, Alembic migrations, auth/JWKS plumbing, the entire
frontend, the corpus-curation scripts (`prefilter_corpus.py`, `judge_corpus_quality.py`,
etc. — good *story*, not ML-core). Know what they do and why; don't rehearse them.

---

## 3. DAYS 1–7

### DAY 1 — System architecture, Docker & how it all connects (~2.5h)
**Objective:** build the mental skeleton first — run the whole stack, watch every
tier connect, trace one request end to end. Everything later hangs on this.
(Cross-reference **§1.5**.)
**Theory:** client–server with token auth; CORS; JWT/JWKS verification; session-
per-request + connection pool; pgvector as the vector store; 3-tier managed deploy.
**Read in order:** `docker-compose.yml` (3 services; internal vs published ports)
→ `backend/app/main.py` (FastAPI app, CORS middleware, router wiring, `/health`)
→ `database.py` (engine, `SessionLocal`, `get_db`) → `auth.py` (JWKS verify + JIT
user provisioning) → frontend `lib/supabase.ts` + `lib/auth.tsx` + `lib/api.ts`
`authHeaders` (client-side Supabase auth, Bearer attach) → `models.py` (the tables
+ the pgvector `Vector(384)` column).
**Active exercises:**
- `docker compose up`; confirm all 3 services healthy; open `localhost:3000`,
  sign in, and watch the browser network tab — see the `Authorization: Bearer …`
  header on a `/me` call and the CORS preflight (OPTIONS) before it.
- Draw **both** diagrams from memory (local 3-service compose + prod 3-tier),
  labeling every arrow with protocol + auth. Diff against §1.5.
- Narrate one authenticated `/me/recommendations` request through all 5 steps
  (browser token → CORS → JWKS verify → `get_db` → service → DB → response).
- Justify aloud: client-side auth vs SSR; stateless backend → scale; pgvector vs
  a vector DB; Render buildpack vs the Dockerfile; `get_db`'s try/finally.
**Interview-defense Q's:**
- "Walk me through your architecture, frontend to database." / "How does the
  frontend authenticate to the backend — what crosses the wire?" / "How is it
  deployed; where do local and prod differ?" (Docker-compose Postgres vs Supabase;
  buildpack vs Dockerfile) / "Why pgvector not a dedicated vector DB — when would
  it break?" / "Your backend is stateless — how do you know and why does it
  matter?" / "What does `get_db` do and why the try/finally?"

### DAY 2 — The deployed core: taxonomy + gap math (~2.5h)
**Objective:** own the thing that actually ships and the product thesis.
**Theory:** coverage/gap/score model; why sum-of-strengths; why the min() cap;
TARGET as a tunable. (No external reading needed — it's your own math.)
**Read in order:** `models.py` (Concept/Book/BookConceptAnnotation, the 48-leaf
taxonomy) → `gap_scoring.py` top-to-bottom (`get_coverage_vector` →
`get_gap_vector` → `score_candidate` → `rank_candidates`).
**Active exercises:**
- Blank file: re-implement all four functions; diff against `gap_scoring.py`.
- By hand: invent a user with 3 books, write their coverage & gap vectors, then
  hand-score 2 candidates with `min(strength, gap)`. Confirm your arithmetic
  against what the formula would produce.
- Change TARGET 2.0→3.0 on paper: what happens to recommendations? (Demands more
  coverage before a concept is "done" → more books recommended per concept.)
**Interview-defense Q's:**
- "Why sum strengths instead of max or count?" / "What breaks if you remove the
  min() cap?" (a strong book over-fills a tiny gap → ranking dominated by
  irrelevant-but-strong books) / "How would you learn TARGET instead of setting
  it?" / "Coverage ignores concept *prerequisites* — is that a flaw?"

### DAY 3 — Stage 1 retrieval + RRF (~2.5h)
**Objective:** explain the 4-retriever pool and the fusion that *is* production.
**Theory:** RRF (Cormack et al. 2009 — read the 1-page formula); bi-encoder ANN
retrieval; why fuse *ranks* not scores.
**Read in order:** `candidate_generation.py` (`generate_candidates` → the 4
source blocks → `_rrf_scored` → `reciprocal_rank_fusion` → `rank_by_hybrid`),
then skim `embedding.py`, `gap_query_embedding.py`, `popularity.py` for what each
retriever contributes.
**Active exercises:**
- Blank file: re-implement `_rrf_scored` from the formula; diff.
- Trace one candidate that appears in 2 of 4 sources by hand: compute its RRF
  score with the real `RRF_WEIGHTS` and `RRF_K_CONSTANT`.
- Articulate each retriever's *bias*: gap=horizon-broadener, embedding_read=
  similar-to-read (trajectory), popularity=crowd, gap_query_embedding=semantic
  gap. Why fuse them?
**Interview-defense Q's:**
- "Why RRF over a weighted score sum?" (scale-free across heterogeneous signals)
- "What does k control?" / "Two retrievers are highly correlated — does RRF
  double-count?" / "How would you tune the weights, and how would you know it
  helped?" (→ your eval) / "What's `recall@k` of this pool and why does it cap
  everything downstream?"

### DAY 4 — CE training *data*: labeling + hard negatives + splits (~3h, hardest)
**Objective:** the label design — where most of the real ML thinking lives.
**Theory:** hard-negative mining (skim DPR, Karpukhin 2020 §"hard negatives");
positive/negative definition; hash-based leakage-free splits.
**Read in order:** `query_builder.py` (the strings) → `generate_training_data.py`
fully: `generate_synthetic_users` → `hold_out` → `classify_held_out` (positive
rules) → `sample_hard_X` (rules d′+e) → `sample_random_negatives` →
`assign_split` → `generate_pairs` driver.
**Active exercises:**
- Blank file: re-implement `classify_held_out` and `sample_hard_X` from the rule
  descriptions in the docstrings; diff. These two are the crux.
- Write, in one paragraph, the precise definition of a hard negative here and
  *why a book the retriever surfaced but that fails the gap rules* is the
  pedagogically useful negative.
- Explain the Phase-3.5 fix (alphabetical tie-break on saturated gaps wrecked
  "top-3 gap concepts" → reformulated to "any gap ≥ threshold"). This is a great
  "I debugged my own labels" story.
**Interview-defense Q's:**
- "How do you prevent train/test leakage with synthetic users?" (user_id hash →
  same user one split; `assert_no_split_leakage` fails the run otherwise)
- "What's a hard negative and why not just random?" / "Your positives are
  *held-out* books that re-appear in the pool — what bias does that introduce?"
  / "Synthetic users — what's the risk vs real users, and why was it acceptable
  for v1?"

### DAY 5 — CE training loop + loss + the reranker inference path (~3h)
**Objective:** rebuild the training loop; defend every hyperparameter; then trace
how the trained model is *served* (Stage 1 → cap → Stage 2) and the parity rule
that keeps train and inference text identical.
**Theory:** sentence-transformers `CrossEncoder.fit`; BCE on a 1-logit head;
warmup + linear schedule; MRR-based checkpointing; reproducibility (seeds +
dataset SHA256 + MLflow); train/inference query-drift.
**Read in order:** `train_cross_encoder.py` (`build_train_examples` →
`build_eval_samples` → `compute_ndcg_at_k` → `main`'s fit block + MLflow logging)
→ `reranker.py` (`rank_by_cross_encoder`: Stage1 `generate_candidates` →
`reciprocal_rank_fusion` → cap to `CROSS_ENCODER_RERANK_K=50` → `CrossEncoder.predict`
→ sort; the `<3 books` cold-start → popularity; RRF fallback when the model is
missing; the process-level singleton load) → `generate_embeddings.py`
(bge-small, 384-dim, IVFFlat built post-load).
**Active exercises:**
- Blank file: write the training skeleton from memory (load → InputExample →
  CrossEncoder(num_labels=1) → DataLoader(shuffle) → warmup_steps calc →
  model.fit with evaluator). Diff against the original.
- Re-derive `warmup_steps = len(loader) * epochs * 0.1` and explain warmup.
- Explain *why val NDCG@10 here is flagged "diagnostic, NOT the success metric"*
  (val negatives are a sampled subset → optimistic; real bar is Phase-5 eval
  against the full RRF pool, NDCG@10 ≥ 0.183).
- Explain the train/inference **query-drift** risk and how `query_builder.py` as
  the single source of truth prevents it (the deep serving point). Justify the
  `K=50` rerank cap and the `<3 books` cold-start fallback.
**Interview-defense Q's:**
- "Why BCE and not a pairwise/listwise ranking loss?" (binary labels; simple,
  stable; ranking loss is the natural v2 — be ready to argue it)
- "What does `num_labels=1` mean mechanically?" / "Why warmup?" / "lr 2e-5,
  batch 8, 3 epochs — defend each / how would you tune them?" / "How is this run
  reproducible?" (seeds, dataset hash, MLflow params)
- Serving: "How does training-time text match inference-time text, and what
  breaks if it doesn't?" / "Cold start — 1 book?" (popularity) / "Model file
  missing in prod?" (latched RRF fallback, no 500) / "Why cap rerank at 50?" /
  "Why IVFFlat built *after* inserts; 384-dim — why, cost to switch to 768?"

### DAY 6 — Evaluation suite + the negative result (~3h, highest interview value)
**Objective:** this is your differentiator — own every metric and the verdict.
**Theory:** NDCG (derive IDCG), MRR, recall@k; graded vs binary relevance;
oracle-normalization; *sequential* vs *static* gain.
**Read in order:** `evaluate_baselines.py` (archetypes, Efraimidis–Spirakis
weighted sampling, `ndcg_at_k`, synthetic-user gen) → `evaluate_gap_fill.py`
(`gain_in_memory`, `apply_book`, `sequential_metrics`, `greedy_oracle_discounted`,
the drift-guard `assert_gain_matches_service`, the fail-loud-if-CE-missing
safeguard) → skim `evaluate_ce_trajectory.py` + `evaluate_hard_negative_rejection.py`
+ `evaluate_candidate_recall.py` for what axis each measures.
**Active exercises:**
- Blank file: `ndcg_at_k` (binary) AND `sequential_metrics` + the greedy oracle.
  Diff. Hand-compute NDCG for held-outs at positions [1,2] then [9,10] (≈1.0 vs
  ≈0.39 — the docstring gives the numbers; verify them).
- Write the 3-sentence version of the negative result: *what* you measured (gap
  closed vs trajectory continuation), *the number* that showed the gap, and *the
  decision* (don't ship CE).
- Explain two safeguards and why they matter: the **drift guard** (in-memory
  gain must equal `score_candidate`) and the **fail-loud** (never score the RRF
  fallback under the `cross_encoder` label).
**Interview-defense Q's:**
- "Walk me through NDCG; derive IDCG for 2 relevant items in top-10."
- "Why a *sequential* gap metric with an oracle instead of plain NDCG?"
  (redundancy: the 2nd chart-patterns book adds ~nothing; static scoring
  over-credits it; oracle gives a fair 1.0 ceiling)
- "You said the CE is a trajectory-continuer — *prove it with a metric*."
- "gap is the upper baseline on your own metric — isn't that circular?" (yes, by
  construction; the informative cells are the *other* strategies vs that ceiling
  — your eval says this explicitly, quote it)
- "How do you know your eval harness itself is correct?" (drift guard, fixed
  seed, held-out fold)

### DAY 7 — Integration + mock defense (~2.5h)
**Objective:** assemble it cold and survive a 20-minute grilling.
**Part A — trace from memory (45 min):** with all files closed, narrate the full
system: read history → gap vector → 4 retrievers → RRF → (research: CE rerank) →
response; then the *offline* loop: synthetic users → labels/hard-negs →
train → eval → the decision. Draw the data-flow diagram from memory; reopen the
map (§1) and correct gaps.
**Part B — mock interview (90 min), answer cold, depth noted:**

*Architecture & systems (≈2 min each):*
1. "What is Atlas and what's the core ML?" → the §0 pitch; lead with RRF-deployed
   + CE-as-negative-result.
2. "Why two-stage retrieve-then-rerank?" → latency/quality; bi vs cross.
3. "Walk me through the architecture frontend-to-DB, and how it's deployed." →
   §1.5: Vercel → Render → Supabase; client-side supabase-js auth; CORS regex;
   `get_db` session-per-request; the prod-vs-local divergences.
4. "How does the frontend authenticate to the backend?" → Bearer JWT from
   supabase-js; backend verifies ES256 via Supabase JWKS; JIT user provisioning.
5. "Why pgvector instead of a dedicated vector DB, and when would you switch?"
6. "Your backend is stateless — defend it and the scaling implication."

*Modeling (≈3–4 min each, deepest):*
7. "Define your training labels — positives, hard negatives, random negatives."
8. "Why BCE? What would a ranking loss change?"
9. "How do you prevent leakage and why does it matter with synthetic users?"
10. "Reproduce the RRF formula and explain k and the weights."

*Evaluation (≈3–4 min each, your strength):*
11. "Derive NDCG@k. Why sequential gap-NDCG with an oracle?"
12. "Prove the cross-encoder is a trajectory-continuer." (cite the eval + number)
13. "Your gap baseline tops your own metric — defend that as honest, not rigged."

*Judgment (≈2 min each):*
14. "You built a model and didn't ship it. Justify." → the senior answer:
    measured the wrong objective being optimized; chose product-aligned RRF.
15. "What's the single biggest weakness of Atlas's ML, and your v2 fix?"
    (e.g. synthetic users / annotation quality / no online metrics — pick one and
    have a concrete fix.)

**Pass bar:** for each, you speak 2–4 min unaided, cite the real file/function,
and when pushed one level deeper you have the answer (the "blind spot" notes in
§2 are exactly where they'll push).

---

## 4. PRIORITIZATION (why this order)
**Day 1 orients you on the system skeleton** (architecture, Docker, how every
tier connects) so the ML days have context and you can answer "walk me through
your system" cold — you asked for this up front, and it pays off all week. The
**ML core is then front-loaded across Days 2–6**: the deployed gap+RRF math
(Days 2–3), label design + training + serving (Days 4–5), and the evaluation +
negative result (Day 6 — your single strongest story). Frontend/auth internals
stay explain-level — standard engineering an interviewer won't grill an ML
candidate on. If you run short on time, **protect Days 4 and 6** — labels and
evaluation are where ML depth is won or lost.

## 5. SOURCES (authoritative, not generic)
- RRF: Cormack, Clarke, Büttcher 2009, "Reciprocal Rank Fusion…" (1-page method).
- Cross-encoder reranking: Nogueira & Cho 2019, "Passage Re-ranking with BERT."
- Bi- vs cross-encoder: Reimers & Gurevych (SBERT) docs + sentence-transformers
  CrossEncoder docs.
- Hard negatives: Karpukhin et al. 2020 (DPR), the hard-negative section.
- NDCG/MRR/recall: any standard IR metrics reference; derive IDCG yourself.
- Your own design docs: `docs/CROSS_ENCODER_DESIGN.md` (§3 query, §6 labels,
  §9 eval), `docs/EVAL_RESULTS.md` (the actual numbers — memorize the headline ones).
```
