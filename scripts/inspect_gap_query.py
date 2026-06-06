"""Manual inspection of gap_query_embedding's top picks.

Generates 4 synthetic users (one per archetype, same seed as the eval),
runs gap_query_embedding for each, and prints their top 10 candidates
with full context for visual review.

Use this to decide whether gap_query_embedding is doing useful semantic
gap-fill work that the synthetic eval cannot measure (because held-outs
are restricted to annotated books).

For each candidate, label visually:
  - good_gap_candidate: actually fills a relevant gap concept
  - plausible_long_tail: semantically relevant, unannotated; good find
  - redundant_similar: too similar to read books, not gap-filling
  - irrelevant_noise: bears no semantic relation to user
  - unknown: can't tell

Decision rules:
  - Most picks = good / plausible -> keep gap_query at weight 0.4
  - Most picks = redundant / noise -> drop gap_query from main pipeline
  - Mixed -> keep low-weight, don't use unannotated picks as hard negatives

Usage:
    python scripts/inspect_gap_query.py
    python scripts/inspect_gap_query.py --top-k 10

Requires DATABASE_URL in env.
"""
import argparse
import os
import random
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402
from backend.app.services.gap_query_embedding import (  # noqa: E402
    TOP_N_GAPS,
    rank_by_gap_query_embedding,
)
from backend.app.services.gap_scoring import get_gap_vector  # noqa: E402
from scripts.evaluate_baselines import (  # noqa: E402
    ARCHETYPES,
    N_USERS_PER_ARCHETYPE,
    RANDOM_SEED,
    generate_synthetic_user,
    load_archetype_weights,
)


DESCRIPTION_LIMIT = 220


def fetch_book_with_annotations(
    session: Session,
    book_id: uuid.UUID,
) -> dict | None:
    """Return title, author, description, and concept annotations for one book."""
    book = session.scalar(select(Book).where(Book.id == book_id))
    if book is None:
        return None

    annotations = session.execute(
        select(Concept.slug, BookConceptAnnotation.strength)
        .join(BookConceptAnnotation, BookConceptAnnotation.concept_id == Concept.id)
        .where(BookConceptAnnotation.book_id == book_id)
        .order_by(BookConceptAnnotation.strength.desc())
    ).all()

    return {
        "title": book.title,
        "author": book.author,
        "description": (book.description or "").strip(),
        "annotations": [(slug, float(strength)) for slug, strength in annotations],
        "is_annotated": len(annotations) > 0,
    }


def print_user_review(
    session: Session,
    archetype: str,
    user_idx: int,
    read_ids: list[uuid.UUID],
    heldout_ids: list[uuid.UUID],
    top_k: int = 10,
) -> None:
    """Print one user's reading context + gap_query's top picks for visual review."""
    print()
    print("=" * 78)
    print(f"USER: {archetype} u{user_idx}")
    print("=" * 78)

    # ---- Reading history ----
    print(f"\n--- READING HISTORY ({len(read_ids)} books) ---")
    for bid in read_ids:
        d = fetch_book_with_annotations(session, bid)
        if d is None:
            continue
        ann_preview = ", ".join(slug for slug, _ in d["annotations"][:3])
        print(f"  - {d['title']} -- {d['author']}")
        if ann_preview:
            extra = "..." if len(d["annotations"]) > 3 else ""
            print(f"    concepts: {ann_preview}{extra}")

    # ---- Top gap concepts (what gap_query queries with) ----
    gap_vector = get_gap_vector(session, read_ids)
    top_gaps = sorted(
        ((slug, gap) for slug, gap in gap_vector.items() if gap > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )[:TOP_N_GAPS]

    print(f"\n--- GAP_QUERY'S TOP {TOP_N_GAPS} QUERY CONCEPTS ---")
    for slug, gap in top_gaps:
        marker = "  <- TIE (saturated)" if gap >= 2.0 else ""
        print(f"  - {slug:50s}  gap={gap:.2f}{marker}")

    # ---- Held-out books ----
    print(f"\n--- HELD-OUT BOOKS ({len(heldout_ids)}) ---")
    heldout_concepts_all = set()
    for bid in heldout_ids:
        d = fetch_book_with_annotations(session, bid)
        if d is None:
            continue
        ann_preview = ", ".join(slug for slug, _ in d["annotations"][:4])
        print(f"  - {d['title']} -- {d['author']}")
        if ann_preview:
            print(f"    concepts: {ann_preview}")
        for slug, _ in d["annotations"]:
            heldout_concepts_all.add(slug)

    # ---- Gap-query's top K picks ----
    print(f"\n--- GAP_QUERY'S TOP {top_k} BOOKS (label each) ---")
    print(f"    good_gap_candidate / plausible_long_tail /")
    print(f"    redundant_similar / irrelevant_noise / unknown")

    ranked = rank_by_gap_query_embedding(session, read_ids, top_k=top_k)
    heldout_set = set(heldout_ids)
    read_concept_set = set()
    for bid in read_ids:
        d = fetch_book_with_annotations(session, bid)
        if d:
            for slug, _ in d["annotations"]:
                read_concept_set.add(slug)

    query_concept_set = {slug for slug, _ in top_gaps}

    for i, (book, score) in enumerate(ranked, start=1):
        d = fetch_book_with_annotations(session, book.id)
        if d is None:
            continue

        # Markers help the eyeball-pass
        is_heldout = book.id in heldout_set
        ann_marker = "[ANN]   " if d["is_annotated"] else "[NO ANN]"
        heldout_marker = "  *** HELD-OUT ***" if is_heldout else ""

        # Does it overlap with query concepts? held-out concepts? read concepts?
        candidate_concepts = {slug for slug, _ in d["annotations"]}
        overlap_query = candidate_concepts & query_concept_set
        overlap_heldout = candidate_concepts & heldout_concepts_all
        overlap_read = candidate_concepts & read_concept_set

        print(f"\n  #{i:2d}  score={score:.4f}  {ann_marker}{heldout_marker}")
        print(f"      {d['title']} -- {d['author']}")
        if d["annotations"]:
            ann_text = ", ".join(
                f"{slug}({strength:.1f})"
                for slug, strength in d["annotations"][:4]
            )
            print(f"      concepts: {ann_text}")
            if overlap_query:
                print(f"      -> overlaps query gaps: {sorted(overlap_query)}")
            if overlap_heldout:
                print(f"      -> overlaps held-out concepts: {sorted(overlap_heldout)}")
            if overlap_read and not overlap_query and not overlap_heldout:
                print(f"      -> ONLY overlaps with read concepts (redundant?): "
                      f"{sorted(overlap_read)}")
        if d["description"]:
            preview = d["description"][:DESCRIPTION_LIMIT]
            if len(d["description"]) > DESCRIPTION_LIMIT:
                preview += "..."
            print(f'      "{preview}"')

        print(f"      LABEL: ____________")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual inspection of gap_query_embedding top picks."
    )
    parser.add_argument("--top-k", type=int, default=10, help="Books per user to show (default 10)")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED, help="Random seed")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    rng = random.Random(args.seed)
    engine = create_engine(db_url)

    with Session(engine) as session:
        # Match the eval's RNG state -- generate all 5 users per archetype but
        # only inspect the first one of each (the "u0" users you saw in the
        # diagnostic).
        for archetype_name, spec in ARCHETYPES.items():
            home_w, sec_w = load_archetype_weights(session, spec)
            first_user_result = None
            for user_idx in range(N_USERS_PER_ARCHETYPE):
                result = generate_synthetic_user(home_w, sec_w, rng)
                if user_idx == 0 and result is not None:
                    first_user_result = result

            if first_user_result is None:
                print(f"[SKIP] {archetype_name}: pool too small for first user")
                continue

            read_ids, heldout_ids = first_user_result
            print_user_review(
                session, archetype_name, 0, read_ids, heldout_ids,
                top_k=args.top_k,
            )

        # ---- Decision guide ----
        print()
        print("=" * 78)
        print("REVIEW COMPLETE -- DECISION GUIDE")
        print("=" * 78)
        print()
        print("Count labels across all 40 candidates (10 per user x 4 users):")
        print()
        print("  good_gap_candidate + plausible_long_tail >= 25 (~63%)")
        print("    -> KEEP gap_query at weight 0.4. Proceed to training data.")
        print()
        print("  irrelevant_noise + redundant_similar >= 25 (~63%)")
        print("    -> DROP gap_query. Revert candidate_generation.py to 3 sources.")
        print()
        print("  Mixed (no clear majority)")
        print("    -> KEEP at low weight BUT note in training data spec:")
        print("      don't use gap_query unannotated picks as hard negatives.")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
