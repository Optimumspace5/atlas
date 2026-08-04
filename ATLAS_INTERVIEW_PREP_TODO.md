# Atlas — 3-Day Interview Prep TODO

> **Source-of-truth note:** `ATLAS_INTERVIEW_DEFENSE.md` was requested as the source
> of truth but **does not exist in this checkout** (searched the whole repo — no
> interview/defense doc anywhere). This TODO is therefore grounded in the **real
> repo** (verified live) + the exact structure enumerated in the task. If the
> defense doc exists on another branch/uncommitted, cross-check section framing —
> but the DB/code facts below are what actually shipped.
>
> **Honesty check (read every time you open this):**
> *If I'm re-reading a doc to feel ready instead of testing myself against the code, I'm doing it wrong.*

---

## DAY 1 — VERIFICATION (2026-07-04) — real results recorded

- [x] **3. Model presence — RESOLVED.** A trained cross-encoder **exists** in this
  checkout: `models/cross_encoder_v1` and `models/cross_encoder_v1_epoch2`.
  → **Decision made: defend RRF-as-default** (correct framing anyway) *with the model
  available* to run the gap-fill eval. **No retrain needed.** Do NOT say "I'd have to
  retrain"; say "the model exists; I chose not to deploy it — here's the measured reason."

- [x] **5. OCR claim — CONFIRMED false-positive-free.** `grep` for
  `tesseract|paddleocr|pytesseract|easyocr|cv2|opencv|ocr` across `backend/` + `scripts/`
  → **none found.** You can say **"OCR ingestion was specced, never built"** with certainty.
  Never imply it exists.

- [x] **1. Double-counting — RAN 2026-08-01: `0 pairs, 0 books affected`.** → **latent-but-not-manifesting.**
  **Structural fact (verified in code):** `book_concept_annotations` PK is
  `(book_id, concept_id, annotation_type)`, and `get_coverage_vector` (`gap_scoring.py`)
  does `SUM(strength)` grouped by concept with **no dedup across annotation_type**. So if a
  `(book, concept)` has rows under >1 type (e.g. `auto` + `manual_grounded`), coverage
  **double-counts**. Run this to learn which defense to rehearse:
  ```powershell
  docker compose up -d postgres
  $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"
  .venv\Scripts\python.exe -c "from sqlalchemy import create_engine,text; c=create_engine('postgresql://atlas:atlas@localhost:5432/atlas_dev').connect(); print('pairs', c.execute(text('select count(*) from (select book_id,concept_id from book_concept_annotations group by book_id,concept_id having count(distinct annotation_type)>1) t')).scalar()); print('books', c.execute(text('select count(distinct book_id) from (select book_id,concept_id from book_concept_annotations group by book_id,concept_id having count(distinct annotation_type)>1) t')).scalar())"
  ```
  - [x] **RESULT (0 pairs) → rehearse "latent-but-not-manifesting":** *"The schema permits it
    and the sum doesn't dedup, so it's a latent risk I'm aware of — but no (book,concept)
    actually has multiple types, so it never fires. I'd harden it with a per-(book,concept)
    max-strength dedup."*
  - [ ] **If pairs > 0 → rehearse "live bug + fix":** *"Coverage currently double-counts N
    pairs across M books; the fix is to dedup per (book,concept), taking one type or the max
    strength."* **Record the real N/M here: __________**

- [ ] **2. Corpus counts — COULD NOT RUN (DB down).** Doc claims 232 curated / 416 annotated
  / 48 leaves. **DB number wins.** Run and record the actuals (any mismatch → say the DB number):
  ```powershell
  .venv\Scripts\python.exe -c "from sqlalchemy import create_engine,text; c=create_engine('postgresql://atlas:atlas@localhost:5432/atlas_dev').connect(); [print(k,c.execute(text(q)).scalar()) for k,q in [('total_books','select count(*) from books'),('curated','select count(*) from books where curated'),('total_annotations','select count(*) from book_concept_annotations'),('annotated_books','select count(distinct book_id) from book_concept_annotations'),('leaf_concepts','select count(*) from concepts where level=1')]]"
  ```
  **RECORDED 2026-08-01:** total=**477** · curated=**232** · annotations=**2894** · annotated_books=**425** · leaves=**48** · curated-annotated=**230**.
  **Mismatch:** doc's "416 annotated" → now **425** (= 416 + 9 grounded must-adds). curated (232) & leaves (48) **match**. If asked, say the live number.

- [ ] **4. Tests — PARTIAL (DB down).** Facts recorded live:
  - App **won't import without `DATABASE_URL`** (`database.py` raises on import) — so always set it before `pytest`.
  - With `DATABASE_URL` set but DB down: **5 ERRORS** = `sqlalchemy.OperationalError` (just the DB being down — not real failures).
  - **2 real FAILURES** in `tests/test_explain_endpoint.py` (`test_explain_404_for_bogus_user`, `..._bogus_book`): they call the **old** `/recommendations/{user_id}/explain` route (now `/me/recommendations/explain` after the auth refactor) → get `404 'Not Found'` instead of the custom detail. **These are stale tests from the /me migration, NOT a live bug.**
  - [x] **RAN 2026-08-01 (DB up): 7 collected · 0 passed · 7 FAILED — ALL stale.** Every test targets the pre-auth routes (`/users/{id}/books`, `/recommendations/{id}/explain`) that the `/me` refactor removed → all 404. **Not functional bugs; the suite was never updated after the auth migration.** Command to re-run:
    ```powershell
    docker compose up -d postgres; $env:DATABASE_URL="postgresql://atlas:atlas@localhost:5432/atlas_dev"; .venv\Scripts\python.exe -m pytest -q
    ```
  - **If asked about tests:** *"Backend has a pytest suite; two explain-endpoint tests are stale from the auth route migration to `/me` — a known cleanup, not a functional bug."* (Don't oversell coverage.)

---

## DAY 2 — UNDERSTANDING PASS (2026-07-05) — "explain from the code, doc CLOSED"

> Rule: doc/notes closed. Open only the named file to self-check *after* you've answered aloud.
> Ordered **hardest-probe first**. Write every stall in the STALL LOG.

- [ ] **A. The objective function — why `min()`, not `sum` or `max`?** *(most-probed core)*
  → Files: `backend/app/services/gap_scoring.py`.
  Explain cold: coverage = `sum(strength)` per concept; gap = `max(0, 2.0 − coverage)`;
  score = `Σ min(candidate_strength, remaining_gap)`. **Why `min`:** a book only fills as much
  gap as is left — a 1.0 book on a 0.5 gap scores 0.5, so strong-but-redundant books can't
  dominate. **Why sum for coverage (not max/count):** captures depth + repetition.
  Done-state: you can walk a 2-book numeric example and justify `min` in one sentence.

- [ ] **B. The trajectory-continuation finding (Phase 5.3) — and why the original gate couldn't catch it.**
  → Files: `docs/EVAL_RESULTS.md` (Phase 5.3, 6.5c), `scripts/evaluate_gap_fill.py`, `scripts/evaluate_baselines.py`.
  Explain cold: the CE learned to recommend books *aligned with what you've already read*
  (trajectory), not gap-fill. The original §9 gate was **held-out NDCG** — "did we rank the
  user's own held-out books high" — which **structurally measures trajectory, not mission**,
  so a trajectory-continuer *passes it*. The mission only became visible with the new
  **sequential gap-NDCG** (6.5c): CE **0.786 (77.4% gap closed)** vs RRF **0.847 (80.1%)** vs
  gap **0.874**. Done-state: you can say *why* held-out recovery ≠ gap-fill without notes.

- [ ] **C. RRF — why ranks not scores, why k=60, and what the weight-tuning regression taught.**
  → Files: `backend/app/services/candidate_generation.py`, `docs/EVAL_RESULTS.md` (Phase 1.5/1.6).
  Explain cold: `score = Σ weight/(60+rank)`; **ranks** because the 4 sources are on
  incompatible scales (gap ~1.5, cosine ~0.8, count ~30) — rank is scale-free; **k=60**
  (Cormack et al.) damps rank-1 dominance. **The regression lesson:** over-weighting
  `gap_query_embedding` (1.2) *dropped* NDCG (−0.045); deweighting to 0.4 restored it — a
  high-recall long-tail source can hurt precision if over-trusted. Done-state: reproduce the
  formula + name the regression takeaway.

- [ ] **D. Training-serving skew — how `query_builder.py` prevents it.**
  → Files: `backend/app/services/query_builder.py`, `backend/app/services/reranker.py`, `scripts/generate_training_data.py`.
  Explain cold: both training-pair generation AND the production reranker call the **same**
  `build_user_query` / `build_candidate_text`. If they drifted, the model would learn one
  string format and be served another → silent degradation. Single source of truth = parity.
  Done-state: you can name the exact failure mode skew causes.

- [ ] **E. Bi-encoder vs cross-encoder, and why two-stage retrieve-then-rerank.** *(commonly asked, lower risk once known)*
  → Files: `backend/app/services/reranker.py` (the Stage1→cap→Stage2 docstring/compose), `candidate_generation.py`.
  Explain cold: bi-encoder embeds query & doc **separately** (precomputable, ANN-searchable, fast,
  used for retrieval); cross-encoder scores the **(query, doc) pair jointly** (accurate, slow, can't
  precompute → only on the top-~50). Two-stage = cheap retrieve, expensive rerank on a shortlist.
  Done-state: one-sentence "why can't you ANN-search a cross-encoder."

- [ ] **F. The four design-decision defenses (each: "why X not Y?").**
  → Files as noted.
  - [ ] **Cross-encoder vs just embeddings for ranking?** (`reranker.py`, `embedding.py`) — CE captures
    query↔doc interaction embeddings miss; used only as a reranker for cost.
  - [ ] **pgvector vs a dedicated vector store (Pinecone/Milvus)?** (`models.py`, `generate_embeddings.py`) —
    one transactional datastore, no sync, joins to book rows; trivial at ~232 books; switch at millions.
  - [ ] **Hand-built taxonomy vs collaborative filtering?** (`gap_scoring.py`, `concepts` schema) — CF needs
    interaction data (cold-start; you had none) and can't express *gaps*; a concept taxonomy makes
    "what you're missing" explicit and works with zero users.
  - [ ] **Why Postgres (at all)?** (`database.py`, `docker-compose.yml`) — relational data + pgvector + auth
    all in one, transactional, simple ops.
  Done-state: for each, one crisp trade-off sentence, not a hedge.

### STALL LOG (write every item you could NOT explain cold — these are your real gaps)
- 
- 
- 

---

## DAY 3 — NARRATIVE + OWNING WEAKNESSES (2026-07-06) — lighter than Day 2

- [ ] **The 2-minute walkthrough — rehearse ALOUD as a spoken checklist** (hit each beat, don't script):
  - [ ] What it is: knowledge-gap-aware book recommender (investing/trading) over a 48-concept taxonomy.
  - [ ] The core: model coverage → gaps → recommend books that fill the biggest gaps (your own algorithm).
  - [ ] Deployed engine: **4-retriever hybrid fused by RRF** (the ML-that-ships).
  - [ ] The reranker: built + fine-tuned a cross-encoder, **measured it, found it optimized trajectory not gap-fill, deliberately didn't ship it.**
  - [ ] Stack, one breath: FastAPI + Postgres/pgvector + Next.js, deployed 3-tier, stateless JWT auth.
  - [ ] Land on the negative result — that's the differentiator.

- [ ] **Lead with the top-3 weaknesses BEFORE they find them** (admission + a fix you can defend — *defending*, not reciting):
  - [ ] **Fabricated frontend "Fit Score."** *Verified:* `frontend/app/recommendations/page.tsx`
    `displayFitScore()` computes the ring value from **rank**, not the real model score.
    **Admit:** "The Fit Score ring is a cosmetic placeholder derived from position, not the actual
    RRF/gap score." **Defend:** "The *ranking* is real; only the displayed number is decorative — I'd
    thread the true score through the API to the UI." (Never present it as a real confidence.)
  - [ ] **Wrong-axis evaluation (the eval that missed the mission).** **Admit:** "My first gate measured
    held-out recovery — which is *trajectory*, not gap-fill — so it couldn't catch that the CE
    optimized the wrong thing." **Defend:** "I built a mission-axis metric (sequential gap-NDCG vs a
    greedy oracle) that made it measurable — 6.5c." (This is maturity, not failure.)
  - [ ] **ML isn't the default.** **Admit:** "The fine-tuned model isn't what serves — RRF is." **Defend:**
    "Because I measured the CE and it closed *less* gap than the simpler hybrid (0.786 vs 0.847); shipping
    it would add complexity for a worse mission outcome. Not shipping it was the right call."

- [ ] **Quick wins — do these FIRST (both trivial, both verified):**
  - [ ] **Empty README.** `README.md` is **0 bytes**. Write ~15 lines: what Atlas is, the stack, how to
    run (docker compose + DATABASE_URL), and the one-line "deployed engine = RRF hybrid; CE measured &
    benched" framing. An interviewer *will* open the README.
  - [ ] **Dead `users.py` router.** `backend/app/routers/users.py` exists but is **not** included in
    `main.py` (the `/me` router replaced it). Either delete it or add a one-line comment marking it
    superseded — so "why is there an unused router?" has a clean answer.

- [ ] **One clean run, aloud:** the 2-min walkthrough **+ the three hardest Day-2 items (A, B, C)** back to
  back, no notes. If any stalls, it goes in the STALL LOG — that's the only thing worth re-drilling.

---

> **Triage reminder:** anything not plausibly asked in a *first-round Singapore tech internship* interview
> was cut. Don't add scope, don't build features — this is defending what exists.
>
> **Final honesty check:** *If I'm re-reading the doc to feel ready instead of testing myself against the code, I'm doing it wrong.*
