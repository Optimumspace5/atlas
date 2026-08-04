# Day 1 — Architecture: Full Q&A Revision Set

The consolidated question + answer for all 6 categories, refined through the
drill. Hand-write each, then reproduce cold. *(Framing reminder: Atlas's deployed
core is the RRF hybrid; the fine-tuned cross-encoder was measured to be a
trajectory-continuer and deliberately NOT deployed — present it as a rigorous
negative result, never "my core.")*

---

## CATEGORY 1 — Topology

**Q: What are the main components of Atlas, and how does it run locally vs in production?**

> Atlas has three application components — a **Next.js** frontend, a **FastAPI**
> backend, and a **Postgres** database (with the **pgvector** extension) — plus
> **Supabase**, a managed cloud service, for authentication. **Locally** these run
> as three **Docker containers** defined in `docker-compose`, where the backend
> waits for Postgres to pass its **healthcheck** before starting (a one-time
> startup gate). Postgres is the primary datastore for all relational data, and
> pgvector lets the embeddings live in that same database. **In production** the
> three roles run as managed services instead: **Vercel** hosts the frontend,
> **Render** the backend, and **Supabase** provides both the database and auth.

**Local diagram:**
```
[ Browser :3000 ] ──HTTP + Bearer JWT──► [ backend :8000 FastAPI ]
       │  └─ login → [ Supabase Auth ☁ ]        │ SQLAlchemy (1 session/req)
       │                  ▲ JWKS verify          ▼
       └──────────────────┴──────── [ postgres (internal) :5432 pgvector ]
```
Port nuance: **browser → backend** via published `localhost:8000`; **backend → DB**
via compose-internal hostname `postgres:5432`.

**Prod diagram:**
```
[ Vercel ☁ Next.js ] ──Bearer JWT──► [ Render ☁ FastAPI ] ──pooler──► [ Supabase ☁ Postgres+pgvector ]
        └──── login ──► [ Supabase Auth ☁ ] ──┘ JWKS verify
```

---

## CATEGORY 2 — Frontend ↔ Backend wiring

**Q 2.1: How does the frontend send a request to the backend?**

> The frontend and backend are **two separate programs** that share no code/memory
> — they communicate only over the network via **HTTP**. When the user does
> something, the frontend's API client (`lib/api.ts`) sends an HTTP request using
> the browser's **`fetch`** to a URL on the backend, e.g. `GET /me/recommendations`.
> It knows the backend's address from the **`NEXT_PUBLIC_API_URL`** env var
> (`localhost:8000` in dev, the Render URL in prod) — a config value, not a secret.
> Each request is a **method + path**: the method is the HTTP verb (`GET` read,
> `POST` create, `DELETE` remove); the path names the endpoint. FastAPI matches
> method+path to one **endpoint** (a single function), organized into **routers**
> (groups under a shared prefix). The endpoint runs its logic, queries Postgres,
> and returns a response — almost always **JSON** (a few return just a status code
> like `204 No Content`).

*Supplementary clarifications:*
- **`fetch`** = the browser's built-in JS function that sends an HTTP request (HTTP = protocol; fetch = the function that speaks it).
- **method = verb (action), path = noun (the thing)** — the path is an *address*, not a journey. `GET /me/books` vs `POST /me/books` = two different endpoints sharing a path.
- **path ↔ routers:** a router has a **prefix**; each endpoint adds a sub-path; full path = prefix + sub-path (e.g. `/me` + `/coverage` = `/me/coverage`). Routers organize endpoints, allow shared config/dependencies, keep `main.py` small. (Folder = router, files = endpoints.)
- A router is **not a runtime gate** — it's organizational (registers routes at startup). At request time FastAPI matches method+path directly to one endpoint function. The request flows: **middleware (CORS) → route match → dependencies → endpoint body → response.**
- An **endpoint = one single-purpose function**; the variety comes from having many endpoints, with the user's action selecting which runs. The frontend fires requests on clicks *or* automatically (e.g. sidebar auto-fetches `/me/coverage` on load).

**Q 2.2: What rides along on a *protected* route, and what makes a route "protected"?**

> A **protected route** declares the `get_current_user` dependency — it needs a
> logged-in user and acts on *that* user's data (all the `/me/…` routes). Public
> routes (`/health`, `/books`, `/concepts`, stateless `POST /recommendations`) need
> no token. On a protected call, the frontend attaches a **JWT (signed access
> token)** in the **`Authorization: Bearer <token>`** header — issued by Supabase at
> login, held by supabase-js, attached by `authHeaders()` in `lib/api.ts`. The
> backend needs it to answer "who is this, and are they logged in?" The token
> carries the user's ID in its **`sub` claim** and is **signed by Supabase**, so it
> can't be forged or altered. The user is always derived from the **token, never the
> URL** — that's why routes are `/me/…`, not `/users/{id}/…`.

**Q 2.3: Why does the browser block cross-origin calls by default, and how does CORS allow it?**

> An **origin** is scheme + host + port, so frontend (`:3000`) and backend (`:8000`)
> are different origins. The browser's **Same-Origin Policy** blocks a page's JS
> from *reading responses from a different origin* — preventing a malicious site
> from using your logged-in session to read another origin's data. **CORS** is the
> opt-in exception: the **called server** declares which origins it trusts via
> response headers, and the **browser** enforces it (allows the read only if the
> origin is permitted). For requests with an `Authorization` header (all `/me`
> calls), the browser first sends an `OPTIONS` **preflight** to check. In Atlas,
> `main.py` adds FastAPI's `CORSMiddleware` with an `ALLOWED_ORIGINS` allow-list
> (`localhost:3000`) plus an `allow_origin_regex` for `atlas-*.vercel.app` (a regex,
> because Vercel assigns a new URL each deploy).

---

## CATEGORY 3 — Authentication

**Q 3.1: What's inside the JWT?**

> A JWT has three parts — **header.payload.signature**. The **payload** holds
> *claims*: user id (`sub`), email, audience (`aud`), and expiry (`exp`). The payload
> is **Base64-encoded, not encrypted** — anyone can read it. Its security comes from
> the **signature**, a cryptographic seal Supabase makes with its private key, so
> **if the token is altered the signature breaks and verification fails**. Tokens are
> **short-lived (~1h)** and auto-refreshed by supabase-js, limiting damage if one leaks.

**Q 3.2: How does the backend verify the token is genuine?**

> Via **asymmetric cryptography**. Supabase signs with a **private key** (secret);
> the backend verifies with the matching **public key**, which can *verify* a
> signature but not *forge* one — so the backend needs **no secret of its own**. It
> gets the public keys from Supabase's **JWKS** endpoint
> (`{SUPABASE_URL}/.well-known/jwks.json`); `SUPABASE_URL` in the env is just the
> *address*. Per request (`auth.py`), `PyJWKClient` fetches the right key by the
> token's **`kid`**, then `jwt.decode` verifies the **ES256** signature and checks
> the audience and expiry — any failure is a **401**.

**Q 3.3: After verification, how does the backend get "your" user, and why is it stateless?**

> It reads the user UUID from the **`sub` claim** and does a **get-or-create on its
> own `users` table** — using the existing row, or creating one on first login
> (**JIT provisioning**), because Supabase owns identity but the app needs its own
> row for foreign keys. The backend is **stateless**: it stores no session — every
> request re-verifies the JWT and re-derives the user from scratch, so the token *is*
> the state. That lets **any instance serve any request** (horizontal scaling) and
> survives restarts without logging anyone out.

---

## CATEGORY 4 — Request lifecycle

**Q 4.1: Trace `GET /me/recommendations` from the frontend to the response.**

1. **Frontend fires it** — `lib/api.ts` `fetch`es `GET /me/recommendations` with the JWT in an `Authorization: Bearer` header.
2. **CORS** — browser sends an `OPTIONS` preflight; `CORSMiddleware` approves the origin; the real `GET` follows.
3. **Route match** — FastAPI matches method+path → the `recommendations` endpoint in the `me` router.
4. **Dependencies run first (dependency injection):**
   - `get_current_user` — verify JWT vs JWKS (ES256), check `aud`/`exp` → 401 if bad; else `sub` → get-or-create user (JIT).
   - `get_db` — yield one pooled SQLAlchemy `Session` for the request.
5. **Endpoint body** — reads the user's `UserBook` ids → gap vector → `generate_candidates` (4-retriever pool) → `reciprocal_rank_fusion`, querying Postgres.
6. **Response** — serialized to JSON by the Pydantic `response_model`; DB session closed in `get_db`'s `finally`.

**One-line order:** `frontend (+JWT) → CORS → route match → dependencies [auth verify → JIT user, then get_db] → endpoint logic + DB queries → JSON response (session closed)`

---

## CATEGORY 5 — Data layer + deployment

**Q 5.1: What are the main tables and how do they relate?**

> Postgres holds: **`books`** (catalog), **`concepts`** (a 2-level taxonomy — ~9
> parent categories, ~48 leaves, linked by a self-referencing `parent_id`),
> **`users`**, and two embedding tables (`book_embeddings`, `concept_embeddings`,
> 384-dim). The heart is two **many-to-many join tables**: **`book_concept_annotations`**
> links books↔concepts with a *strength* (what each book teaches), and
> **`user_books`** links users↔books (reading history). Gap scoring walks
> `user_books → book_concept_annotations`, aggregating strengths per concept into
> **coverage**, then **gap = target − coverage**.

**Q 5.2: What does pgvector add, and why not a dedicated vector DB?**

> pgvector is a Postgres **extension** that adds a `vector` column type **and**
> similarity search — distance operators (cosine, L2) plus an ANN index (**IVFFlat**)
> to find nearest vectors fast. Atlas stores 384-dim embeddings this way and
> retrieves nearest books by cosine distance. I use it over a dedicated vector DB
> because it keeps everything in **one transactional datastore** — no second system
> to deploy or sync, vectors join straight to book rows — and at ~232 books it's
> trivially fast. I'd switch to Pinecone/Milvus only at much larger scale (millions
> of vectors, high throughput, advanced ANN), where a purpose-built index outperforms.

**Q 5.3: How is Atlas deployed (local vs prod), and what are the gotchas?**

> Locally it runs as three Docker containers (Postgres, FastAPI, Next.js); in
> production it's three managed tiers — **Vercel** (frontend), **Render** (backend),
> **Supabase** (Postgres+pgvector). Supabase is the **auth provider in both**, but
> the **database only in prod** (locally the DB is the Docker Postgres). Gotchas:
> Render runs the **Python buildpack, not the Dockerfile** (to avoid shipping torch);
> `docker-compose` omits `SUPABASE_URL`/Supabase keys, so dev needs explicit env
> vars; prod uses Supabase's **session-pooler** connection and a **CORS regex** for
> the Vercel domains; and Render's free tier **cold-starts ~50s** after idle.

---

## CATEGORY 6 — Lock it in (recall test, no new content)

**Q: From memory, (a) draw both topology diagrams, and (b) narrate the full request lifecycle cold.**

This is the test that Day 1 stuck — the "answers" are the **Category 1 diagrams**
(local 3-container + prod 3-tier, above) and the **Category 4 request lifecycle**
(the 6 steps + one-line order, above). Do it closed-book; whatever you can't
reproduce is exactly what to re-drill.

**Pass bar for Day 1:** redraw both diagrams, narrate the request trace unaided,
and answer Q1.1 → Q5.3 cold, each in 4–8 sentences, citing the real file/mechanism.
