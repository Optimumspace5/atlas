"""Phase 1 + 1.5 + 2 candidate recall baseline + rank diagnostics
+ gap-query alignment audit.

Measures per-source and union recall, rank distributions, and NDCG@10
on synthetic archetype users for TWO pool orderings:

  1. Insertion order (current generate_candidates output —
     gap first, then gap_query, then embedding_read, then popularity)
  2. Reciprocal Rank Fusion (RRF — weighted rank fusion across sources)

Plus a gap-query diagnostic: are the concepts gap_query embedding
queries actually aligned with the concepts of the user's held-out books?
Tests the hypothesis that tied gap values (gap = COVERAGE_TARGET for many
unconsidered concepts) cause gap_query to pick semi-random query slugs.

See docs/CROSS_ENCODER_DESIGN.md Section 8 for the recall quality gate.

Usage:
    python scripts/evaluate_candidate_recall.py

Requires DATABASE_URL in env. Logs to MLflow experiment
candidate_recall_v1 under ./mlruns/.
"""
import math
import os
import random
import statistics
import sys
import uuid
from pathlib import Path

import mlflow
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import BookConceptAnnotation, Concept  # noqa: E402
from backend.app.services.candidate_generation import (  # noqa: E402
    Candidate,
    SOURCE_ORDER,
    generate_candidates,
    reciprocal_rank_fusion,
)
from backend.app.services.gap_query_embedding import TOP_N_GAPS  # noqa: E402
from backend.app.services.gap_scoring import COVERAGE_TARGET, get_gap_vector  # noqa: E402
from scripts.evaluate_baselines import (  # noqa: E402
    ARCHETYPES,
    N_USERS_PER_ARCHETYPE,
    RANDOM_SEED,
    generate_synthetic_user,
    load_archetype_weights,
)


RECALL_KS = [10, 20, 50, 100, 150]
NDCG_K = 10
MLFLOW_EXPERIMENT = "candidate_recall_v1"
UNION_RECALL_AT_100_GATE = 0.90


def ndcg_at_k(
    ranked_ids: list[uuid.UUID],
    relevant_ids: list[uuid.UUID],
    k: int = NDCG_K,
) -> float:
    """Standard NDCG@k with binary relevance."""
    relevant_set = set(relevant_ids)
    dcg = 0.0
    for i, bid in enumerate(ranked_ids[:k], start=1):
        if bid in relevant_set:
            dcg += 1.0 / math.log2(i + 1)
    n_rel = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_rel + 1))
    return dcg / idcg if idcg > 0 else 0.0


def oracle_ndcg_at_k(
    pool_ids: list[uuid.UUID],
    relevant_ids: list[uuid.UUID],
    k: int = NDCG_K,
) -> float:
    """If a perfect reranker put all relevant items first, what's NDCG@k?"""
    pool_set = set(pool_ids)
    in_pool = sum(1 for r in relevant_ids if r in pool_set)
    in_pool_capped = min(in_pool, k)
    oracle_dcg = sum(1.0 / math.log2(i + 1) for i in range(1, in_pool_capped + 1))
    n_rel = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_rel + 1))
    return oracle_dcg / idcg if idcg > 0 else 0.0


def heldout_ranks_in_pool(
    pool_ids: list[uuid.UUID],
    relevant_ids: list[uuid.UUID],
) -> list[int | None]:
    """For each relevant id, return its 1-indexed rank in the pool, or None."""
    ranks: list[int | None] = []
    for rid in relevant_ids:
        try:
            ranks.append(pool_ids.index(rid) + 1)
        except ValueError:
            ranks.append(None)
    return ranks


def get_gap_query_concepts(
    session: Session,
    read_book_ids: list[uuid.UUID],
) -> list[str]:
    """Reproduce the slug selection that rank_by_gap_query_embedding uses.

    Returns the top TOP_N_GAPS concept slugs the user's gap_query was built
    from. Sort by gap value descending; tie-break is dict iteration order
    (potentially non-deterministic across runs if multiple concepts share
    the max gap).
    """
    if not read_book_ids:
        return []
    gap_vector = get_gap_vector(session, read_book_ids)
    top = sorted(
        ((slug, gap) for slug, gap in gap_vector.items() if gap > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )[:TOP_N_GAPS]
    return [slug for slug, _ in top]


def get_book_concepts(
    session: Session,
    book_ids: list[uuid.UUID],
) -> set[str]:
    """Return the set of concept slugs annotated against any of these books."""
    if not book_ids:
        return set()
    rows = session.execute(
        select(Concept.slug)
        .join(BookConceptAnnotation, BookConceptAnnotation.concept_id == Concept.id)
        .where(BookConceptAnnotation.book_id.in_(book_ids))
        .distinct()
    ).scalars().all()
    return set(rows)


def gap_distribution(
    session: Session,
    read_book_ids: list[uuid.UUID],
) -> dict:
    """Count partial vs saturated gaps for the user.

    A 'partial gap' is one where 0 < gap < COVERAGE_TARGET (concept the
    user has touched but not completed). A 'saturated gap' is gap ==
    COVERAGE_TARGET (concept the user has not touched at all). Partial
    gaps are more actionable — they represent natural-progression
    recommendations.
    """
    if not read_book_ids:
        return {"partial": 0, "saturated": 0, "covered": 0}
    gap_vector = get_gap_vector(session, read_book_ids)
    partial = sum(1 for g in gap_vector.values() if 0 < g < COVERAGE_TARGET)
    saturated = sum(1 for g in gap_vector.values() if g >= COVERAGE_TARGET)
    covered = sum(1 for g in gap_vector.values() if g <= 0)
    return {"partial": partial, "saturated": saturated, "covered": covered}


def measure_recall_for_user(
    pool: list[Candidate],
    heldout_ids: list[uuid.UUID],
) -> dict:
    """For one user, compute recall + rank + NDCG metrics from a generated pool."""
    heldout_set = set(heldout_ids)
    n_heldout = len(heldout_set)
    pool_ids = [c.book_id for c in pool]

    # Union recall at each K (order-sensitive)
    union_recall = {}
    for K in RECALL_KS:
        slice_set = set(pool_ids[:K])
        hits = sum(1 for hid in heldout_ids if hid in slice_set)
        union_recall[K] = hits / n_heldout if n_heldout > 0 else 0.0

    # Per-source recall (order-independent)
    per_source_recall = {}
    per_source_pool_size = {}
    for source in SOURCE_ORDER:
        source_ids = {c.book_id for c in pool if source in c.sources}
        per_source_pool_size[source] = len(source_ids)
        hits = sum(1 for hid in heldout_ids if hid in source_ids)
        per_source_recall[source] = hits / n_heldout if n_heldout > 0 else 0.0

    # Per-source unique contribution (order-independent)
    per_source_unique = {source: 0 for source in SOURCE_ORDER}
    for c in pool:
        if len(c.sources) == 1:
            per_source_unique[c.sources[0]] += 1

    # Held-out ranks in union (order-sensitive)
    heldout_union_ranks = heldout_ranks_in_pool(pool_ids, heldout_ids)

    # Per-source held-out ranks (order-independent — uses Candidate rank fields)
    rank_attr = {
        "gap": "gap_rank",
        "gap_query_embedding": "gap_query_rank",
        "embedding_read": "embedding_rank",
        "popularity": "popularity_rank",
    }
    per_source_heldout_ranks: dict[str, list[int | None]] = {}
    book_id_to_candidate = {c.book_id: c for c in pool}
    for source in SOURCE_ORDER:
        attr = rank_attr[source]
        ranks: list[int | None] = []
        for hid in heldout_ids:
            cand = book_id_to_candidate.get(hid)
            ranks.append(getattr(cand, attr) if cand else None)
        per_source_heldout_ranks[source] = ranks

    # NDCG: current ordering and oracle ceiling
    ndcg = ndcg_at_k(pool_ids, heldout_ids, k=NDCG_K)
    oracle = oracle_ndcg_at_k(pool_ids, heldout_ids, k=NDCG_K)

    return {
        "n_heldout": n_heldout,
        "union_recall": union_recall,
        "per_source_recall": per_source_recall,
        "per_source_pool_size": per_source_pool_size,
        "per_source_unique": per_source_unique,
        "heldout_union_ranks": heldout_union_ranks,
        "per_source_heldout_ranks": per_source_heldout_ranks,
        "ndcg_at_10": ndcg,
        "oracle_ndcg_at_10": oracle,
        "union_pool_size": len(pool),
    }


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    rng = random.Random(RANDOM_SEED)
    engine = create_engine(db_url)

    with Session(engine) as session:
        # Build synthetic users (same as evaluate_baselines.py)
        users = []
        for archetype_name, spec in ARCHETYPES.items():
            home_w, sec_w = load_archetype_weights(session, spec)
            for user_idx in range(N_USERS_PER_ARCHETYPE):
                result = generate_synthetic_user(home_w, sec_w, rng)
                if result is None:
                    print(f"[SKIP] {archetype_name} user {user_idx}: pool too small")
                    continue
                read_ids, heldout_ids = result
                users.append({
                    "archetype": archetype_name,
                    "user_idx": user_idx,
                    "read_ids": read_ids,
                    "heldout_ids": heldout_ids,
                })
        print(f"Generated {len(users)} synthetic users.")

        # For each user: generate pool, measure metrics for BOTH orderings
        results_insertion = []
        results_rrf = []
        for user in users:
            insertion_pool = generate_candidates(session, user["read_ids"])
            rrf_pool = reciprocal_rank_fusion(insertion_pool)

            m_ins = measure_recall_for_user(insertion_pool, user["heldout_ids"])
            m_ins["archetype"] = user["archetype"]
            m_ins["user_idx"] = user["user_idx"]
            results_insertion.append(m_ins)

            m_rrf = measure_recall_for_user(rrf_pool, user["heldout_ids"])
            m_rrf["archetype"] = user["archetype"]
            m_rrf["user_idx"] = user["user_idx"]
            results_rrf.append(m_rrf)

            run_name = f"{user['archetype']}__u{user['user_idx']}"
            with mlflow.start_run(run_name=run_name):
                mlflow.log_params({
                    "archetype": user["archetype"],
                    "user_idx": user["user_idx"],
                    "n_heldout": m_ins["n_heldout"],
                })
                for K, r in m_ins["union_recall"].items():
                    mlflow.log_metric(f"union_recall_at_{K}", r)
                    mlflow.log_metric(f"rrf_union_recall_at_{K}", m_rrf["union_recall"][K])
                for source, r in m_ins["per_source_recall"].items():
                    mlflow.log_metric(f"{source}_recall", r)
                mlflow.log_metric("union_pool_size", m_ins["union_pool_size"])
                mlflow.log_metric("ndcg_at_10", m_ins["ndcg_at_10"])
                mlflow.log_metric("rrf_ndcg_at_10", m_rrf["ndcg_at_10"])
                mlflow.log_metric("oracle_ndcg_at_10", m_ins["oracle_ndcg_at_10"])

        # ---- Gap-query diagnostic ----
        # The reviewer's hypothesis: if multiple concepts tie at gap=TARGET,
        # gap_query picks 5 essentially at random. If those random concepts
        # don't overlap with the held-outs' concepts, gap_query is querying
        # the wrong things.
        gq_diagnostics = []
        for user in users:
            query_concepts = get_gap_query_concepts(session, user["read_ids"])
            heldout_concepts = get_book_concepts(session, user["heldout_ids"])
            gap_dist = gap_distribution(session, user["read_ids"])

            query_set = set(query_concepts)
            overlap = query_set & heldout_concepts
            union = query_set | heldout_concepts
            jaccard = len(overlap) / len(union) if union else 0.0

            gq_diagnostics.append({
                "archetype": user["archetype"],
                "user_idx": user["user_idx"],
                "query_concepts": query_concepts,
                "heldout_concepts": sorted(heldout_concepts),
                "overlap_count": len(overlap),
                "overlap_slugs": sorted(overlap),
                "jaccard": jaccard,
                "partial_gaps": gap_dist["partial"],
                "saturated_gaps": gap_dist["saturated"],
            })

        print()
        print("=" * 78)
        print("GAP-QUERY DIAGNOSTIC (is gap-query querying the right concepts?)")
        print("=" * 78)

        mean_overlap = statistics.mean(d["overlap_count"] for d in gq_diagnostics)
        mean_jaccard = statistics.mean(d["jaccard"] for d in gq_diagnostics)
        mean_partial = statistics.mean(d["partial_gaps"] for d in gq_diagnostics)
        mean_saturated = statistics.mean(d["saturated_gaps"] for d in gq_diagnostics)
        n_zero_overlap = sum(1 for d in gq_diagnostics if d["overlap_count"] == 0)

        print(f"\nAGGREGATE:")
        print(f"  Mean overlap (query concepts ∩ held-out concepts): {mean_overlap:.2f} / 5")
        print(f"  Mean Jaccard similarity:                            {mean_jaccard:.4f}")
        print(f"  Users with ZERO overlap:                            {n_zero_overlap}/{len(gq_diagnostics)}")
        print(f"  Mean partial gaps per user (0 < gap < TARGET):      {mean_partial:.1f}")
        print(f"  Mean saturated gaps per user (gap == TARGET):       {mean_saturated:.1f}")
        print(f"  → If saturated is high, gap_query is picking from ties.")

        # Sample 4 users (one per archetype) for visual inspection
        print(f"\nSAMPLE (one user per archetype — visually check alignment):")
        seen_archetypes = set()
        for d in gq_diagnostics:
            if d["archetype"] in seen_archetypes:
                continue
            seen_archetypes.add(d["archetype"])
            print(f"\n  [{d['archetype']} u{d['user_idx']}]")
            print(f"    query gaps: {d['query_concepts']}")
            print(f"    held-out concepts: {d['heldout_concepts'][:8]}"
                  f"{' (truncated)' if len(d['heldout_concepts']) > 8 else ''}")
            print(f"    overlap: {d['overlap_slugs'] if d['overlap_slugs'] else '(none)'}")
            print(f"    gap distribution: partial={d['partial_gaps']}, "
                  f"saturated={d['saturated_gaps']}")

        # ---- Console summary ----
        print()
        print("=" * 78)
        print("PHASE 1 + 1.5 + 2: insertion-order vs RRF on the same candidate pool")
        print("=" * 78)

        n = len(results_insertion)
        mean_pool = statistics.mean(r["union_pool_size"] for r in results_insertion)
        print(f"\nN users measured: {n}")
        print(f"Mean union pool size: {mean_pool:.1f} candidates / user")

        # Side-by-side recall comparison
        print("\nUNION RECALL @ K  (insertion-order  vs  RRF):")
        print(f"{'K':>5}  {'insertion':>10}  {'RRF':>10}  {'Δ':>10}")
        for K in RECALL_KS:
            ins = statistics.mean(r["union_recall"][K] for r in results_insertion)
            rrf = statistics.mean(r["union_recall"][K] for r in results_rrf)
            print(f"{K:>5}  {ins:>10.4f}  {rrf:>10.4f}  {rrf - ins:>+10.4f}")

        # NDCG comparison
        ndcg_ins = statistics.mean(r["ndcg_at_10"] for r in results_insertion)
        ndcg_rrf = statistics.mean(r["ndcg_at_10"] for r in results_rrf)
        oracle = statistics.mean(r["oracle_ndcg_at_10"] for r in results_insertion)
        print(f"\nNDCG@{NDCG_K}  (insertion-order  vs  RRF  vs  oracle ceiling):")
        print(f"  insertion order : {ndcg_ins:.4f}")
        print(f"  RRF             : {ndcg_rrf:.4f}  ({ndcg_rrf - ndcg_ins:+.4f} lift)")
        print(f"  oracle (perfect): {oracle:.4f}")
        if oracle > ndcg_ins:
            print(f"  RRF reclaims {(ndcg_rrf - ndcg_ins) / (oracle - ndcg_ins) * 100:.1f}% "
                  f"of the available NDCG gap.")

        # Held-out rank comparison
        def collect_ranks(results, key):
            r = []
            missing = 0
            for u in results:
                for rank in u[key]:
                    if rank is None:
                        missing += 1
                    else:
                        r.append(rank)
            return r, missing

        ranks_ins, miss_ins = collect_ranks(results_insertion, "heldout_union_ranks")
        ranks_rrf, miss_rrf = collect_ranks(results_rrf, "heldout_union_ranks")

        print("\nHELD-OUT RANK in pool  (insertion-order  vs  RRF):")
        print(f"{'stat':<12}  {'insertion':>10}  {'RRF':>10}")
        if ranks_ins and ranks_rrf:
            print(f"{'mean':<12}  {statistics.mean(ranks_ins):>10.1f}  "
                  f"{statistics.mean(ranks_rrf):>10.1f}")
            print(f"{'median':<12}  {statistics.median(ranks_ins):>10.1f}  "
                  f"{statistics.median(ranks_rrf):>10.1f}")
            print(f"{'min':<12}  {min(ranks_ins):>10}  {min(ranks_rrf):>10}")
            print(f"{'max':<12}  {max(ranks_ins):>10}  {max(ranks_rrf):>10}")
            print(f"{'missing':<12}  {miss_ins:>10}  {miss_rrf:>10}")

        # Per-source held-out ranks
        print(f"\nPER-SOURCE HELD-OUT RANKS (rank within source's top-K, intrinsic):")
        print(f"{'source':<22}  {'mean':>8}  {'median':>8}  {'min':>6}  "
              f"{'max':>6}  {'n found':>8}")
        for source in SOURCE_ORDER:
            ranks = []
            for r in results_insertion:
                for rank in r["per_source_heldout_ranks"][source]:
                    if rank is not None:
                        ranks.append(rank)
            if ranks:
                print(
                    f"{source:<22}  "
                    f"{statistics.mean(ranks):>8.1f}  "
                    f"{statistics.median(ranks):>8.1f}  "
                    f"{min(ranks):>6}  "
                    f"{max(ranks):>6}  "
                    f"{len(ranks):>8}"
                )
            else:
                print(f"{source:<22}  (no held-outs surfaced by this source)")

        # Per-source recall
        print("\nPER-SOURCE RECALL (at each source's configured top-K):")
        print(f"{'source':<22}  {'mean':>8}  {'std':>8}  {'pool size':>10}")
        for source in SOURCE_ORDER:
            recalls = [r["per_source_recall"][source] for r in results_insertion]
            sizes = [r["per_source_pool_size"][source] for r in results_insertion]
            mean = statistics.mean(recalls)
            std = statistics.stdev(recalls) if len(recalls) > 1 else 0.0
            mean_size = statistics.mean(sizes)
            print(f"{source:<22}  {mean:>8.4f}  {std:>8.4f}  {mean_size:>10.1f}")

        # Per-source unique contribution
        print("\nPER-SOURCE UNIQUE CONTRIBUTION (mean # books found ONLY by this source):")
        for source in SOURCE_ORDER:
            uniques = [r["per_source_unique"][source] for r in results_insertion]
            mean = statistics.mean(uniques)
            print(f"  {source:<22}  {mean:>6.1f} books / user")

        # Quality gate
        print()
        print("=" * 78)
        gate_ins = statistics.mean(r["union_recall"][100] for r in results_insertion)
        gate_rrf = statistics.mean(r["union_recall"][100] for r in results_rrf)
        gate_passed = max(gate_ins, gate_rrf) >= UNION_RECALL_AT_100_GATE
        verdict = "PASS" if gate_passed else "FAIL"
        print(
            f"QUALITY GATE: union recall@100 >= {UNION_RECALL_AT_100_GATE:.2f}\n"
            f"  insertion: {gate_ins:.4f}  RRF: {gate_rrf:.4f}  → [{verdict}]"
        )

        # Summary MLflow run
        with mlflow.start_run(run_name="summary"):
            mlflow.set_tag("is_summary", "true")
            mlflow.log_params({
                "n_users": n,
                "seed": RANDOM_SEED,
                "sources": ",".join(SOURCE_ORDER),
                "phase": "2_audit_gap_query_diagnostic",
            })
            for K in RECALL_KS:
                ins = statistics.mean(r["union_recall"][K] for r in results_insertion)
                rrf = statistics.mean(r["union_recall"][K] for r in results_rrf)
                mlflow.log_metric(f"mean_union_recall_at_{K}", ins)
                mlflow.log_metric(f"mean_rrf_union_recall_at_{K}", rrf)
            for source in SOURCE_ORDER:
                vals = [r["per_source_recall"][source] for r in results_insertion]
                mlflow.log_metric(f"mean_{source}_recall", statistics.mean(vals))
            mlflow.log_metric("mean_union_pool_size", mean_pool)
            mlflow.log_metric("mean_ndcg_at_10", ndcg_ins)
            mlflow.log_metric("mean_rrf_ndcg_at_10", ndcg_rrf)
            mlflow.log_metric("mean_oracle_ndcg_at_10", oracle)
            mlflow.log_metric("rrf_ndcg_lift", ndcg_rrf - ndcg_ins)
            mlflow.log_metric("gap_query_mean_overlap", mean_overlap)
            mlflow.log_metric("gap_query_zero_overlap_users", n_zero_overlap)
            mlflow.log_metric("mean_partial_gaps", mean_partial)
            mlflow.log_metric("mean_saturated_gaps", mean_saturated)
            mlflow.log_metric("gate_passed", float(gate_passed))

        print(f"\nMLflow runs at: ./mlruns/  (run `mlflow ui` to browse)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
