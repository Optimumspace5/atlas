"""Phase 6.3: validate the auto-annotator against the 60 manual books.

Runs annotate_book() on every book that already has manual annotations,
compares predicted concepts to the manual ground truth, and reports
precision / recall / F1 / strength agreement plus a disagreement audit.

Precision is weighted as the metric that matters: a false concept corrupts
gap scoring; a missed one is harmless. But the manual set is itself
incomplete (humans tagged the obvious concepts), so low precision may mean
the model is RIGHT where the human was lazy — the disagreement CSV is
where you decide.

Run once per model and compare:
    python scripts/validate_annotator.py --model sonnet
    python scripts/validate_annotator.py --model opus

Requires DATABASE_URL and ANTHROPIC_API_KEY in env.
"""
import argparse
import csv
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path

from anthropic import Anthropic
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402
from scripts.auto_annotate import (  # noqa: E402
    annotate_book,
    load_taxonomy,
    resolve_model,
)


def manual_books_with_truth(
    session: Session,
) -> list[tuple[Book, dict[str, float]]]:
    """Every book with >=1 HUMAN annotation, paired with {slug: strength}.

    Excludes annotation_type='auto' — after the Phase 6.4 load, the DB
    contains model-written annotations, and validating the model against
    its own output would be circular.
    """
    annotated_ids = list(session.execute(
        select(BookConceptAnnotation.book_id)
        .where(BookConceptAnnotation.annotation_type != "auto")
        .distinct()
    ).scalars().all())
    out: list[tuple[Book, dict[str, float]]] = []
    for bid in annotated_ids:
        book = session.scalar(select(Book).where(Book.id == bid))
        if book is None:
            continue
        rows = session.execute(
            select(Concept.slug, BookConceptAnnotation.strength)
            .join(BookConceptAnnotation, BookConceptAnnotation.concept_id == Concept.id)
            .where(BookConceptAnnotation.book_id == bid)
            .where(BookConceptAnnotation.annotation_type != "auto")
        ).all()
        truth = {slug: float(s) for slug, s in rows}
        out.append((book, truth))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate auto-annotator vs manual ground truth.")
    parser.add_argument("--model", default="sonnet", help="sonnet | opus | haiku | raw-id")
    parser.add_argument("--limit", type=int, default=None, help="only first N books (quick check)")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        return 2

    model = resolve_model(args.model)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    engine = create_engine(db_url)

    with Session(engine) as session:
        taxonomy_block, valid_slugs = load_taxonomy(session)
        books_truth = manual_books_with_truth(session)
        if args.limit:
            books_truth = books_truth[: args.limit]
        print(f"Validating {model} against {len(books_truth)} manually-annotated books ...\n")

        # micro counters
        tp = fp = fn = 0
        strength_match = strength_total = 0
        pred_count_total = truth_count_total = 0
        per_book_pr: list[tuple[float, float]] = []
        disagreements: list[dict] = []

        for i, (book, truth) in enumerate(books_truth, 1):
            preds, _warns = annotate_book(client, book, taxonomy_block, valid_slugs, model)
            pred_map = {p.concept_slug: p.strength for p in preds}
            rationale_map = {p.concept_slug: p.rationale for p in preds}

            pred_slugs = set(pred_map)
            truth_slugs = set(truth)
            b_tp = pred_slugs & truth_slugs
            b_fp = pred_slugs - truth_slugs
            b_fn = truth_slugs - pred_slugs

            tp += len(b_tp); fp += len(b_fp); fn += len(b_fn)
            pred_count_total += len(pred_slugs)
            truth_count_total += len(truth_slugs)

            for slug in b_tp:
                strength_total += 1
                if pred_map[slug] == truth[slug]:
                    strength_match += 1

            p = len(b_tp) / len(pred_slugs) if pred_slugs else 0.0
            r = len(b_tp) / len(truth_slugs) if truth_slugs else 0.0
            per_book_pr.append((p, r))

            for slug in b_fp:
                disagreements.append({
                    "book": book.title, "author": book.author, "concept_slug": slug,
                    "status": "FP", "pred_strength": pred_map[slug], "truth_strength": "",
                    "rationale": rationale_map.get(slug, ""),
                })
            for slug in b_fn:
                disagreements.append({
                    "book": book.title, "author": book.author, "concept_slug": slug,
                    "status": "FN", "pred_strength": "", "truth_strength": truth[slug],
                    "rationale": "",
                })

            print(f"  [{i}/{len(books_truth)}] {book.title[:42]:<42} "
                  f"P={p:.2f} R={r:.2f}  (pred {len(pred_slugs)}, truth {len(truth_slugs)})")

        # ---- Aggregate ----
        micro_p = tp / (tp + fp) if (tp + fp) else 0.0
        micro_r = tp / (tp + fn) if (tp + fn) else 0.0
        micro_f1 = (2 * micro_p * micro_r / (micro_p + micro_r)) if (micro_p + micro_r) else 0.0
        macro_p = sum(p for p, _ in per_book_pr) / len(per_book_pr) if per_book_pr else 0.0
        macro_r = sum(r for _, r in per_book_pr) / len(per_book_pr) if per_book_pr else 0.0
        strength_agree = strength_match / strength_total if strength_total else 0.0

        print()
        print("=" * 72)
        print(f"VALIDATION RESULTS — {model}")
        print("=" * 72)
        print(f"Books: {len(books_truth)}")
        print(f"Concept pairs:  TP={tp}  FP={fp}  FN={fn}")
        print()
        print(f"  micro precision: {micro_p:.4f}")
        print(f"  micro recall:    {micro_r:.4f}")
        print(f"  micro F1:        {micro_f1:.4f}")
        print(f"  macro precision: {macro_p:.4f}  (mean per-book)")
        print(f"  macro recall:    {macro_r:.4f}  (mean per-book)")
        print(f"  strength agreement (on TP concepts): {strength_agree:.4f} "
              f"({strength_match}/{strength_total})")
        print(f"  avg concepts/book:  predicted {pred_count_total/len(books_truth):.1f}  "
              f"truth {truth_count_total/len(books_truth):.1f}")
        print()

        out_csv = REPO_ROOT / "data" / f"validate_annotator_{args.model}.csv"
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "book", "author", "concept_slug", "status",
                "pred_strength", "truth_strength", "rationale",
            ])
            writer.writeheader()
            for d in disagreements:
                writer.writerow(d)

        fps = [d for d in disagreements if d["status"] == "FP"]
        fns = [d for d in disagreements if d["status"] == "FN"]
        print(f"Disagreements: {len(fps)} FP (model added), {len(fns)} FN (model missed)")
        print(f"Full audit CSV: {out_csv}")
        print()
        print("Sample FALSE POSITIVES (model assigned, human didn't — right or wrong?):")
        for d in fps[:12]:
            print(f"  {d['book'][:34]:<34} + {d['concept_slug']:<40} ({d['pred_strength']})")
            if d["rationale"]:
                print(f"      reason: {d['rationale'][:90]}")
        print("\nSample FALSE NEGATIVES (human assigned, model missed):")
        for d in fns[:12]:
            print(f"  {d['book'][:34]:<34} - {d['concept_slug']:<40} ({d['truth_strength']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
