# Atlas

**A knowledge-gap-aware book recommender for investing and trading — it recommends what you're *missing*, not more of what you've already read.**

A deployed, multi-user web app: backend on Render, frontend on Vercel, Postgres + pgvector on Supabase.

---

## The idea

Most recommenders optimize *similarity* or *popularity*: read a few technical-analysis
books and you get more technical-analysis books. For someone trying to actually *learn* a
field, that's a failure mode — you deepen what you already know while staying blind to
whole areas (risk management, valuation, market structure).

Atlas maps your reading history onto a **48-concept taxonomy** of investing/trading,
measures which concepts you've under-covered, and ranks books by how much of that **gap**
they would close — optimizing for breadth of understanding rather than similarity.

---

## How it works

1. **Taxonomy.** Every book is annotated with the concepts it teaches and how strongly
   (`1.0` major topic, `0.5` secondary), across 48 leaf concepts.
2. **Coverage → Gap.** For a reader, *coverage* per concept = the summed strength of the
   books they've read; *gap* = `max(0, TARGET − coverage)` (TARGET = 2.0). A concept you've
   never touched has the maximum gap; one you've covered has none.
3. **Candidate scoring.** A book's score = `Σ min(book_strength, remaining_gap)` over its
   concepts. The `min` caps each term at the gap that's actually left, so a book strong on
   something you've already covered can't dominate. *(`backend/app/services/gap_scoring.py`.)*
4. **Hybrid retrieval.** Four retrievers each return a ranked shortlist — **gap scoring**,
   a **gap-query embedding** search, an **embedding-similarity** retriever, and
   **popularity** — deduplicated into one pool of ~110 candidates.
   *(`backend/app/services/candidate_generation.py`.)*
5. **RRF fusion.** The four rankings are fused with **Reciprocal Rank Fusion**
   (`score = Σ weight/(60 + rank)`), which combines lists by *rank* rather than raw score,
   so signals on incompatible scales (a gap score, a cosine similarity, a popularity count)
   fuse fairly. This fused hybrid is the **production default**.
6. **Cross-encoder reranker (built, not deployed).** A fine-tuned `bge-reranker-base` can
   rerank the top candidates as an optional Stage 2 — but evaluation showed it optimized the
   wrong objective, so it is *not* the served strategy (see below).

Depth: `docs/SPEC.md`, `docs/CROSS_ENCODER_DESIGN.md`, `docs/SCHEMA.md`.

---

## The honest result

Evaluation is on **synthetic reader profiles** (4 archetypes) — see `docs/EVAL_RESULTS.md`
for full tables and per-archetype slices.

On the **mission metric** — *sequential gap-fill NDCG@10*, which credits each recommended
book for the gap it closes, depletes the gap as books are "read," and normalizes against a
greedy oracle:

| Strategy | seq gap-NDCG@10 | avg. gap closed |
|---|---|---|
| gap (mission upper-baseline) | 0.874 | 78.3% |
| **RRF hybrid (deployed)** | **0.847** | **80.1%** |
| popularity | 0.799 | 77.6% |
| cross-encoder | 0.786 | 77.4% |
| embedding / tf-idf | 0.25 / 0.23 | ~25% |

**Why the cross-encoder is built but not deployed.** The fine-tuned `bge-reranker-base`
(trained with binary cross-entropy on ~1,858 synthetic pairs) was evaluated and found to
have learned **trajectory-continuation** — recommending books aligned with the user's
*existing* reading — rather than gap-fill. On the mission metric it closes *less* gap than
the simpler RRF hybrid (0.786 vs 0.847). Deploying it would add complexity for a worse
outcome on the objective the product actually cares about, so **RRF is the production
default and the cross-encoder is kept as an offline result.** This was a deliberate,
evidence-backed decision, documented in `docs/EVAL_RESULTS.md` (Phase 5.3, 6.5c).

---

## Tech stack

*Verified against `backend/requirements*.txt` and `frontend/package.json`.*

- **Frontend:** Next.js 16 (React 19, TypeScript), Tailwind CSS 4, `@supabase/supabase-js`
  (client-side auth), `lucide-react`. Deployed on **Vercel**.
- **Backend:** FastAPI + Uvicorn, SQLAlchemy 2.0 + Alembic (migrations), Pydantic,
  PyJWT (JWKS / ES256 verification), Anthropic SDK (the "explain this recommendation"
  feature), scikit-learn / NumPy. Deployed on **Render**.
- **Database:** PostgreSQL 15 + **pgvector** (384-dim embeddings stored alongside the
  relational data). Hosted on **Supabase**, which also provides authentication.
- **ML / offline** (`backend/requirements.txt`, not shipped to the web service):
  `sentence-transformers` (PyTorch) for embeddings and the cross-encoder; MLflow for
  experiment tracking in the training scripts.
- **Embedding model:** `BAAI/bge-small-en-v1.5` (384-dim). **Reranker:**
  `BAAI/bge-reranker-base`, fine-tuned (checkpoint gitignored, regeneratable).

---

## Architecture (request flow)

```
[ Browser / Next.js (Vercel) ]
        │  HTTP + "Authorization: Bearer <JWT>"
        ▼
[ CORS middleware ] ── preflight ──► allow known origins (localhost + *.vercel.app)
        ▼
[ get_current_user ] ── verify JWT vs Supabase JWKS (ES256) ──► [ Supabase Auth ]
        │  (stateless: no server session; user JIT-provisioned on first login)
        ▼
[ FastAPI /me/* endpoint ]  (routers: me, books, concepts, recommendations)
        ▼
[ gap vector (from reading history) ]
        ▼
[ generate_candidates ] ─ 4 retrievers ─► gap · gap_query_embedding · embedding_read · popularity
        ▼
[ reciprocal_rank_fusion ]  ──►  top-k books
        ▼
[ JSON response (Pydantic) ]   ← Postgres + pgvector throughout
```

The fine-tuned cross-encoder (`reranker.py`) is an *optional* Stage 2 after RRF; it is not
the served default.

---

## Project structure

```
backend/    FastAPI app (app/routers, app/services), SQLAlchemy models, Alembic migrations
frontend/   Next.js 16 app (App Router), client-side Supabase auth
scripts/    Data pipeline + ML: corpus fetch/curation, annotation, embeddings,
            cross-encoder training, and the evaluation harness (evaluate_*.py)
data/       Curated catalog, annotations, training pairs, evidence (CSV / JSONL)
docs/       SPEC, SCHEMA, CROSS_ENCODER_DESIGN, EVAL_RESULTS, and design notes
models/     Fine-tuned cross-encoder checkpoints (gitignored)
```

Core recommendation logic lives in `backend/app/services/` — `gap_scoring.py`,
`candidate_generation.py`, `reranker.py`, `query_builder.py`.

---

## Running it locally

**Prerequisites:** Docker, Python 3.11+, Node 18+, and a Supabase project (for auth + the
JWKS endpoint). Env vars: `DATABASE_URL`, `SUPABASE_URL`, `NEXT_PUBLIC_API_URL`,
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

```bash
# 1. Database (Postgres + pgvector) via Docker
docker compose up -d postgres

# 2. Backend
export DATABASE_URL="postgresql://atlas:atlas@localhost:5432/atlas_dev"
export SUPABASE_URL="https://<your-project>.supabase.co"
pip install -r backend/requirements.txt
cd backend && python -m alembic upgrade head && cd ..
python -m uvicorn backend.app.main:app --reload --port 8000

# 3. Frontend (frontend/.env.local holds the NEXT_PUBLIC_* vars)
cd frontend && npm install && npm run dev   # http://localhost:3000
```

> **Note on `docker-compose.yml`:** it defines all three services, but only `postgres` is
> used above. The compose `backend`/`frontend` services don't set the Supabase env vars, so
> authenticated routes require the manual env-var setup shown here. Data-pipeline and
> evaluation scripts under `scripts/` need `DATABASE_URL` and, for the ML scripts, the
> offline dependencies in `backend/requirements.txt`.

---

## What's built vs. known limitations

**Built and working:** the full pipeline above, deployed and multi-user (Supabase JWT
auth, per-user libraries, coverage/gaps, hybrid recommendations, an LLM "explain this
recommendation" endpoint), a curated corpus (**232 curated books** of 477 collected; **48**
concepts; ~2,900 annotations), and the offline cross-encoder training + evaluation suite.

**Honest limitations:**
- **Evaluation is synthetic.** All metrics come from synthetic archetype users — there is
  no real-usage data yet. Validating against real users is the top next step.
- **The cross-encoder is a trajectory-continuer** (see above) — a real negative result,
  which is why it isn't deployed.
- **The backend test suite is stale.** The 7 tests target pre-auth routes
  (`/users/{id}/...`) that the multi-user `/me` refactor replaced, so they currently fail
  against removed routes — a known cleanup item, not a functional regression.
- **The frontend "Fit Score"** shown per recommendation is a cosmetic value derived from
  rank, not the model's score; the *ranking* is real, the displayed number is decorative.
- **Small scale, no prerequisite modeling.** The corpus is ~232 curated books, and gap
  scoring treats concepts independently (it doesn't order by prerequisites yet); an
  investor/trader learning-roadmap feature is an early step toward that.

---

## Evaluation methodology

The harness (`scripts/evaluate_*.py`) reports two axes: a **trajectory** axis (held-out
recovery NDCG@10) and a **mission** axis. The mission metric is the more novel piece —
**sequential gap-fill NDCG@10**: each ranked book is credited `min(strength, current gap)`,
the gap is then depleted (so a second book on an already-covered concept earns almost
nothing — it penalizes redundancy), and the result is normalized against a **greedy oracle**
that fills the most gap at each step. Gap scoring is the upper baseline by construction; the
informative comparison is how the other strategies — especially the cross-encoder — measure
against that ceiling. Full tables and the two-axis finding are in **`docs/EVAL_RESULTS.md`**.

---

*Author: Clarence Lee. Built as a solo project to learn ML engineering end-to-end —
retrieval, ranking, evaluation, and deployment.*
