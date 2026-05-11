# Atlas — Project Specification
**Version:** v0.1  
**Date:** 2026-05-11  
**Author:** Clarence Lee  
**Status:** Draft

---

## 1. Problem Statement

Self-directed learners in investing and trading rely on books to build their knowledge, but existing recommenders — driven by similarity or popularity — cannot tell a user whether a new book fills a missing area in their understanding. In a domain where knowledge is cumulative and conceptually interconnected, this leads to a predictable failure: users repeatedly read books covering familiar territory while remaining blind to gaps in areas like risk management, valuation, or market structure. Atlas solves this by mapping a user's reading history against a hand-built taxonomy of investing and trading concepts — with explicit prerequisite and adjacency relationships — identifying under-covered areas, and ranking recommendations by coverage gain rather than similarity. The result is more intentional reading progression and fewer redundant books.

---

## 2. Success Metrics

### 2.1 Ranking Quality
Atlas should achieve NDCG@10 ≥ 0.4 on a hand-labeled evaluation set and outperform popularity-based and similarity-only baselines by at least 15–20% relative improvement.

**Evaluation method:** 20–30 synthetic reader profiles, each manually labeled on a 0–3 relevance scale before any model output is observed. NDCG@10 is computed for Atlas and both baselines (popularity ranker, content-similarity ranker) and compared.

### 2.2 Gap Coverage Effectiveness
At least 80% of the top 10 recommendations should contain at least one taxonomy tag corresponding to a concept node identified as under-covered in the user's reading profile. Atlas should also produce higher average coverage gain than the similarity-only baseline.

**Evaluation method:** For each synthetic profile, under-covered concept nodes are identified from the taxonomy coverage map. Each recommended book's taxonomy tags are checked against those nodes. A book counts toward the 80% threshold if it contains at least one matching under-covered node. Books are tagged conservatively to avoid inflating this metric.

### 2.3 System Latency
For manual book input, Atlas should return ranked recommendations within 4 seconds at P95 latency, excluding OCR processing and third-party metadata API delays.

**Evaluation method:** Latency is measured across the core pipeline — gap detection, ranking, and explanation generation — not end-to-end wall clock time. OCR and external API calls (Google Books, Open Library) are excluded as they are network-dependent and outside system control.

### 2.4 User Validation
In a small user study with 3–5 investing/trading learners, Atlas should achieve an average recommendation usefulness rating of ≥4/5, with a majority of users preferring Atlas recommendations over a similarity-only baseline in a blind comparison. Given the small sample size, this metric is intended as indicative product validation rather than statistically conclusive evidence.

**Evaluation method:** Users are shown Atlas output and similarity-only output side by side without being told which is which, then asked to rate usefulness and state a preference. Qualitative feedback is also collected.

---

## 3. Non-Goals (v1)

### 3.1 Collaborative Filtering
Atlas does not use collaborative filtering in v1. Without sufficient user interaction data at launch, collaborative signals would be sparse, unreliable, and misaligned with Atlas's core objective of recommending books based on conceptual knowledge gaps rather than crowd behavior.

### 3.2 Philosophy or Other Reading Domains
Atlas does not support philosophy or other non-investing/trading domains in v1. Restricting the system to investing and trading allows the taxonomy, book tagging, gap detection, and evaluation process to be deeper and more coherent instead of spreading the project across multiple domains too early.

### 3.3 Messy or Angled Shelf Image Input
Atlas does not aim to support messy, angled, poorly lit, or highly cluttered bookshelf images in v1. Robust recognition from difficult shelf images would require a more advanced computer vision pipeline and would distract from the project's primary recommender-system objective.

### 3.4 Full Computer Vision Book Detection
Atlas does not include custom object detection or full computer vision-based book spine detection in v1. The image-input feature is limited to clean book covers or neat shelf photos because the project's main technical focus is knowledge-gap-aware recommendation, not building a general-purpose visual book recognition system.

### 3.5 Social Features
Atlas does not include social features such as user profiles, friend activity, public reading lists, reviews, comments, or community recommendations in v1. These features would add product complexity without directly improving the core evaluation question: whether taxonomy-driven gap detection produces better recommendations than similarity-based methods.

### 3.6 Multi-Domain Support
Atlas does not support multiple knowledge domains in v1. Multi-domain recommendation would require separate taxonomies, tagging logic, evaluation datasets, and domain-specific assumptions, which would significantly increase scope before the investing/trading recommender has been proven to work.
