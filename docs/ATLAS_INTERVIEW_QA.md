# Atlas — Interview Q&A (SG internship prep)

Clear, speakable answers to the questions most likely to come up, each with the
**key terms defined**. Say them in your own words — don't recite. Numbers are from
the real repo/eval.

**The one framing to never break:** the *deployed* engine is the **RRF hybrid**; the
fine-tuned cross-encoder was **measured, found to optimize the wrong objective, and
deliberately not shipped.** Present that as a rigorous *negative result*, never as
"my core model."

---

## 1. Project overview

**Q: Tell me about Atlas / walk me through a project you're proud of.**
> "Atlas is a **knowledge-gap-aware book recommender** for investing and trading.
> Instead of recommending *more of what you've already read* like a normal
> similarity recommender, it models your knowledge as a vector over a 48-concept
> taxonomy, finds the concepts you're *missing*, and recommends books that fill
> those gaps. The engine that actually ships is a **4-retriever hybrid fused with
> Reciprocal Rank Fusion**. I also built and fine-tuned a **cross-encoder reranker**
> on top — but when I evaluated it I found it optimized the wrong objective, so I
> deliberately kept it out of production. It's full-stack — FastAPI, Postgres with
> pgvector, Next.js, deployed on three tiers — but the interesting part is the
> recommendation core and the evaluation."

*Key terms:*
- **Taxonomy** — a fixed, structured list of concepts (here, 48 leaf topics like *trend analysis*, *position sizing*) used as the "map" of knowledge.
- **Retriever** — a component that produces a ranked shortlist of candidate items.
- **Reranker** — a second, more accurate model that re-orders a shortlist.
- **Full-stack** — frontend (UI) + backend (server/API) + database.

**Q: How is this different from a normal recommender?**
> "A normal recommender optimizes *similarity* — you liked technical-analysis books,
> here are more. Atlas optimizes *breadth*: it looks at the whole concept map, sees
> what you've never covered, and pushes you toward it. The thesis is that a good
> *learning* recommender should round out your knowledge, not echo it back."

---

## 2. The core algorithm — gap scoring

**Q: How does the recommendation actually work?**
> "Three steps. **Coverage**: for each of the 48 concepts, I sum the strengths of the
> books you've read that teach it — strength is 1.0 for a major topic, 0.5 for a
> secondary one. **Gap**: `max(0, 2.0 − coverage)`, floored at zero — a concept you've
> never read has the max gap, one you've covered has none. **Score**: for a candidate
> book, I sum `min(its strength on a concept, your remaining gap on that concept)`
> across its concepts. Highest score = fills the most gap = recommended."

**Q: Why `min()`, not just add the strengths?**
> "Because a book can only fill *as much gap as is left*. A deep 1.0-strength book on
> a concept where you only have 0.5 of gap should get credit for 0.5, not 1.0.
> Without the cap, a book that's very strong on something you've *already covered*
> would rack up a big score and dominate — backwards for a gap-filler. The cap makes
> the score reflect *useful* gap-filling, not raw strength."

**Q: Why sum for coverage, not max or count?**
> "Sum captures both depth and repetition — two solid books cover a concept more than
> one. Max ignores repetition; count ignores how strongly each book teaches it."

*Key terms:*
- **Coverage / gap vector** — a list of 48 numbers, one per concept, saying how much you know / how much you're missing.
- **Strength** — how strongly a book teaches a concept (1.0 major, 0.5 secondary).
- **Annotation** — a labeled link "this book teaches this concept at this strength."

---

## 3. Retrieval + RRF

**Q: How do you generate the candidate books to rank?**
> "Four retrievers, each returning a ranked shortlist: **gap scoring** (the mission
> signal), a **gap-query embedding** search (same intent in embedding space, so it
> works even where annotations are sparse), an **embedding-similarity** retriever
> (books similar to what you've read — a trajectory signal), and **popularity** (a
> broad, safe fallback). I dedupe them into one pool of ~110 books and fuse the
> rankings with RRF. Each has a different bias, so fusing covers each other's blind
> spots."

**Q: What is Reciprocal Rank Fusion, and why fuse *ranks* not scores?**
> "RRF merges several ranked lists into one. Each item scores `Σ weight / (60 + rank)`
> over the lists it appears in — an item near the top of *multiple* lists floats up.
> I use **rank, not the raw score**, because the four signals are on incompatible
> scales — a gap score ~1.5, a cosine similarity ~0.8, a popularity count ~30 —
> adding those is meaningless, but rank position is scale-free. The **k=60** (from the
> Cormack RRF paper) damps rank-1 so it doesn't utterly dominate rank-2. And the
> fused hybrid beat *every single source* — it closed 80% of the gap volume."

**Q: What is recall@k and where does it matter?**
> "Recall@k is: of the items I should find, what fraction land in the top-k. In my
> pipeline it applies to **Stage 1** — did the *right* book even make it into the
> ~110-book pool? It's the **ceiling on everything downstream**: if a book isn't
> retrieved, no reranker can recover it. So I measure candidate recall separately —
> in one regime 70% of target books never entered the pool, which caps quality no
> matter how good the reranker is."

*Key terms:*
- **RRF (Reciprocal Rank Fusion)** — a rank-based method to combine multiple ranked lists into one.
- **Embedding** — a list of numbers (a vector) representing the meaning of text, so similar meanings are close together.
- **Dedupe** — remove duplicates (a book found by several retrievers appears once).
- **recall@k** — fraction of the correct items that appear in the top-k results.

---

## 4. Bi-encoder, cross-encoder, two-stage

**Q: What's the difference between a bi-encoder and a cross-encoder?**
> "A **bi-encoder** embeds the query and each document *separately* into vectors, so
> similarity is a cheap vector comparison — and since each document's vector can be
> precomputed and indexed, you can search the whole corpus fast. A **cross-encoder**
> feeds the `(query, document)` pair through *one* model together, so they interact
> through attention — much more accurate, but you have to run it on every pair at
> query time, so it's too slow for the whole corpus."

**Q: Why two-stage retrieve-then-rerank?**
> "You can't run the expensive cross-encoder over thousands of books. So the cheap
> bi-encoder retrieval narrows the corpus to a ~100-book pool, and the cross-encoder
> reranks only the top ~50. You get the bi-encoder's speed and the cross-encoder's
> accuracy where it matters. You *can't* ANN-search a cross-encoder because its score
> depends on the pair — there's no standalone document vector to index."

**Q: What are embeddings / what's pgvector?**
> "An embedding turns text into a vector so similar meanings sit close together — I
> use `bge-small`, 384 dimensions. **pgvector** is a Postgres extension that stores
> those vectors in a column *and* does nearest-neighbour search over them with an
> index. So my embeddings live in the same database as everything else."

*Key terms:*
- **Attention** — the mechanism that lets a transformer weigh how much each word relates to every other word.
- **ANN (Approximate Nearest Neighbour)** — fast "find the closest vectors" search that trades a little accuracy for speed.
- **Dimension (384-dim)** — the length of the embedding vector.
- **pgvector** — a Postgres extension adding a vector column type + similarity search.

---

## 5. Evaluation + the negative result (your strongest story)

**Q: What are you most proud of / what was hard?**
> "The most interesting result was a **negative** one. I fine-tuned a cross-encoder
> expecting it to improve recommendations. But on a metric I designed to measure
> *gap-fill* specifically, it closed *less* of the user's gap than the simpler hybrid.
> It had learned to **continue the user's trajectory** — recommend more of what they
> already read — instead of *broadening* them, which is the whole point. So I chose
> **not to deploy it**. Building something, measuring it honestly, finding it solved
> the wrong problem, and having the discipline to bench it — that's what I learned
> most from."

**Q: How did you *measure* that it was a trajectory-continuer?**
> "I built a **sequential gap-NDCG** metric. I walk the top-10 recommendations, and for
> each book I credit `min(strength, current gap)` — then I *decrement* the gap, as if
> you'd read it. So the second book on a concept you've already filled gets almost no
> credit — the metric **penalizes redundancy**. I normalize against a greedy oracle
> that picks the best gap-filler at each step. On that metric the cross-encoder scored
> **0.786 (77.4% of gap closed)** versus the hybrid's **0.847 (80.1%)** — quantitative
> proof it wasn't filling gaps."

**Q: What is NDCG? What is MRR?**
> "**NDCG** is a ranking-quality score from 0 to 1 that rewards putting valuable items
> near the top, with a discount for lower positions. It's your ranking's DCG divided
> by the ideal ranking's DCG, so 1.0 means perfect ordering. **MRR** — Mean Reciprocal
> Rank — is the average of `1 / (rank of the first correct item)`; it rewards getting
> one right answer high up. I used MRR to pick the best training checkpoint."

**Q: Isn't it circular that your gap strategy tops your own gap metric?**
> "Yes — by construction, and I'm explicit about it. The gap strategy directly
> optimizes that objective, so it's the **upper baseline**, not a fair competitor. The
> point of the metric isn't to crown gap; it's to measure how close the *other*
> strategies — especially the cross-encoder — get to that ceiling. So it's a
> deliberate yardstick, not a rigged contest."

*Key terms:*
- **NDCG (Normalized Discounted Cumulative Gain)** — ranking quality 0–1: are valuable items near the top, discounted by position.
- **MRR (Mean Reciprocal Rank)** — average of 1/(rank of first correct item).
- **Trajectory-continuation** — recommending "what's next on your current path" (vs gap-fill = "what you're missing").
- **Greedy oracle** — the best-possible ordering, used as the 1.0 ceiling for normalization.

---

## 6. Training data

**Q: How did you create training data with no real users?**
> "I generated **synthetic users** from four reader archetypes — each samples a reading
> history weighted toward its concepts. For each user I **hold out** a few books as
> **positives** — but only if the held-out book strongly teaches a concept the user
> actually has a gap in, so the label means 'good gap-fill,' not just 'they read it.'
> The important part is the **hard negatives**: books a retriever surfaced — so they
> *look* relevant — but that fail the gap rules. Those teach the model the real
> decision boundary; random negatives only teach it to reject obvious junk."

**Q: What loss did you use, and why?**
> "Binary cross-entropy. The model outputs a single number, sigmoided to a relevance
> score in [0,1], and my labels are binary — positive or negative — so BCE fits
> directly. A ranking loss would be the natural next step since it optimizes relative
> order, but BCE was simpler and matched my clean binary labels."

**Q: How did you prevent train/test leakage and overfitting?**
> "For **leakage**, I split by *user* — I hash the user id into a bucket so *all* of a
> user's examples land in the same split; a user never straddles train and test, and
> I assert that at training start. For **overfitting**, small dataset, so I kept it
> light — three epochs, a small learning rate with warmup, and a held-out test split I
> never touched — plus MLflow logging so runs are reproducible."

*Key terms:*
- **Synthetic users** — fabricated users (from archetype sampling) used because there was no real usage data.
- **Positive / negative** — a training example labeled "good recommendation" (1) or "bad" (0).
- **Hard negative** — a wrong answer that *looks* right (surfaced by retrieval), used to teach the fine decision boundary.
- **BCE (Binary Cross-Entropy)** — a loss for binary labels; penalizes predicted probability far from the 0/1 target.
- **Leakage** — test information sneaking into training, inflating metrics dishonestly.
- **Overfitting** — the model memorizing training data instead of generalizing.
- **Epoch** — one full pass over the training data.
- **Learning rate / warmup** — how big the update steps are; warmup ramps it up gently at the start.

---

## 7. Systems & deployment

**Q: How is it deployed?**
> "Three managed tiers: **Vercel** for the Next.js frontend, **Render** for the FastAPI
> backend, and **Supabase** for Postgres-with-pgvector *and* auth. The backend is
> **stateless** — it verifies a JWT on every request against Supabase's public keys —
> so any instance can serve any request and it scales horizontally."

**Q: How does authentication work?**
> "Supabase issues a **JWT** — a signed token — when the user logs in. The frontend
> attaches it to each request as a `Bearer` header. The backend verifies the
> **signature** against Supabase's public keys, so it trusts the token without storing
> any session, then reads the user id from it. If the token's tampered with, the
> signature check fails."

**Q: Why Postgres/pgvector instead of a dedicated vector DB?**
> "It keeps the vectors in the *same* database as the relational data — one
> transactional store, no second system to sync, vectors join straight to book rows.
> At ~232 books it's trivially fast. I'd switch to Pinecone or Milvus only at millions
> of vectors or high throughput, where a purpose-built index earns its keep."

*Key terms:*
- **Stateless** — the server keeps no per-user session; every request proves its own identity, so any instance can handle any request.
- **JWT (JSON Web Token)** — a signed, tamper-proof token carrying the user's identity.
- **Signature / public key** — the cryptographic seal that proves the token is genuine and unaltered.
- **Bearer header** — the HTTP header (`Authorization: Bearer <token>`) that carries the JWT.

---

## 8. Weaknesses & reflection (lead with these before they find them)

**Q: What's the biggest weakness / what would you improve?**
> "The training data is **synthetic users** — I had no real usage — so the model risks
> learning my archetype distribution rather than real behavior. Top v2 priority is
> real users, instrument what they add and click, and re-evaluate on that. Second, the
> gap engine ignores concept **prerequisites** — it might recommend an advanced topic
> before the fundamentals — which I started addressing with a learning-roadmap
> feature that orders concepts into a path."

**Q: (Own it first) Is that "Fit Score" in the UI real?**
> "No — I should flag that. The Fit Score ring on the frontend is a **cosmetic
> placeholder** computed from the item's rank, not the real model score. The *ranking*
> is real; only the displayed number is decorative. I'd thread the true RRF/gap score
> through the API to the UI to make it honest."

**Q: If you had more time?**
> "Two things: close the loop with real users and a proper online metric, since all my
> evaluation is offline and synthetic; and revisit the reranker with a **ranking loss**
> and gap-aware labels — the cross-encoder failed because it optimized trajectory, so a
> reranker trained to directly optimize gap-fill might actually beat the hybrid."

---

## 9. General ML fundamentals (answer with an Atlas hook)

**Q: How do you handle overfitting?**
> "Fewer epochs, a small learning rate, regularization, and a held-out test set. In
> Atlas: small data, so 3 epochs + warmup + a test split I never touched."

**Q: Explain the precision/recall trade-off.**
> "Precision = of what you predicted positive, how much was right; recall = of the true
> positives, how many you caught. Raising one usually lowers the other. In Atlas, my
> web-grounded annotator hit F1 0.809 vs 0.702 for the bulk one — the gain was mostly
> **recall** (it found concepts the blurb-only version missed)."

**Q: What is a train/validation/test split?**
> "Train fits the model, validation tunes choices like which checkpoint to keep, test
> is a final untouched estimate of real performance. In Atlas I split 80/10/10 by user
> hash so the same user never crosses splits."

*Key terms:*
- **Precision** — of the items you flagged positive, the fraction actually positive.
- **Recall** — of all the true positives, the fraction you found.
- **F1** — the harmonic mean of precision and recall (one balanced number).
- **Validation set** — held-out data used to tune choices *during* development (not train, not final test).
- **Regularization** — techniques that discourage the model from over-fitting.

---

## QUICK GLOSSARY (drill these cold)

| Term | One-line definition |
|---|---|
| Embedding | A vector representing text meaning; similar meanings sit close. |
| Bi-encoder | Embeds query & doc *separately* → fast, precomputable retrieval. |
| Cross-encoder | Scores the (query, doc) pair *jointly* → accurate, slow rerank. |
| RRF | Fuses ranked lists via `Σ weight/(k+rank)`; uses rank, scale-free. |
| recall@k | Fraction of correct items in the top-k (the Stage-1 ceiling). |
| NDCG | Ranking quality 0–1, position-discounted, vs the ideal ordering. |
| MRR | Average of 1/(rank of first correct item). |
| Hard negative | A wrong answer that *looks* right; teaches the decision boundary. |
| BCE | Binary cross-entropy loss for 0/1 labels. |
| Leakage | Test info contaminating training → dishonest metrics. |
| pgvector | Postgres extension: vector column + similarity search. |
| JWT | Signed, tamper-proof token carrying the user's identity. |
| Stateless | Server stores no session; each request proves its own identity. |
| Trajectory-continuation | Recommending "what's next" vs gap-fill "what's missing." |

---

**How to use this:** read a Q, cover the answer, say it aloud in your own words, then
check. Over-rehearse **Sections 1, 2, and 5** — the pitch, the gap algorithm, and the
negative result — those carry ~80% of any project deep-dive.
