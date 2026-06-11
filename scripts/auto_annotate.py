"""Auto-annotation core: assign taxonomy concepts to a book via Claude.

Shared library for Phase 6. Both the validation harness
(validate_annotator.py) and the batch run import annotate_book() from
here, so the prompt the validator tests is the exact prompt the batch
uses — no drift.

Validation status (Phase 6.3, 60 manual books, claude-sonnet-4-6, THIS
v1 prompt): micro precision 0.752, recall 0.659, F1 0.702, strength
agreement 0.690, zero empty books. A stricter v2 prompt was tested and
REJECTED (recall collapsed to 0.536). Do not tighten rule 1 without
re-running validate_annotator.py.

The batch main() writes data/auto_annotations_v1.csv (CSV only — it never
touches the DB; loading is a separate step). Crash-safe: progress is
tracked in a .done sidecar, so --resume continues a interrupted run
without re-paying for completed books.

Strength scale (v1): only 1.0 (confirmed) and 0.5 (weak). Tangential
concepts are omitted entirely, never 0.3.

Usage:
    python scripts/auto_annotate.py --model sonnet --limit 5   # smoke
    python scripts/auto_annotate.py --model sonnet             # full ~408
    python scripts/auto_annotate.py --model sonnet --resume    # continue
    python scripts/auto_annotate.py --model sonnet --overwrite # fresh start

Requires DATABASE_URL and ANTHROPIC_API_KEY in env.
"""
import argparse
import csv
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic, APIError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, BookConceptAnnotation, Concept  # noqa: E402


MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "sonnet"
MAX_TOKENS = 1024
VALID_STRENGTHS = {1.0, 0.5}
OUTPUT_CSV = REPO_ROOT / "data" / "auto_annotations_v1.csv"


# v1 prompt — the validated configuration (see docstring). Rule 1's
# "when in doubt, OMIT" is the precision lever; tightening it further
# was measured to collapse recall.
SYSTEM_PROMPT = (
    "You are a financial-literature cataloguer for Atlas, a knowledge-gap-"
    "aware book recommender. Given a book's title, author, and description, "
    "you assign the concepts from a fixed taxonomy that the book genuinely "
    "teaches.\n\n"
    "Rules:\n"
    "1. Assign a concept ONLY if the book actively teaches or substantially "
    "covers it. When in doubt, OMIT it. Precision matters far more than "
    "recall: a wrong concept corrupts downstream scoring, a missing one is "
    "harmless.\n"
    "2. Use ONLY concept slugs from the provided taxonomy. Never invent a "
    "slug.\n"
    "3. Strength is exactly one of:\n"
    "   - 1.0  a major, recurring topic the book actively teaches\n"
    "   - 0.5  a real but secondary topic, or one whose depth you cannot "
    "verify from the description\n"
    "   Do NOT include tangential mentions at any strength: omit them.\n"
    "4. Base your judgment on the title, author, and description. You may "
    "apply general knowledge to interpret them, but every annotation's "
    "rationale must be defensible from this information.\n"
    "5. Output ONLY a JSON object, no prose and no markdown fences:\n"
    '   {"annotations": [{"concept_slug": "...", "strength": 1.0, '
    '"rationale": "<one sentence grounded in the description>"}]}\n'
    '   If no concepts apply, return {"annotations": []}.'
)


@dataclass
class ParsedAnnotation:
    concept_slug: str
    strength: float
    rationale: str


def load_taxonomy(session: Session) -> tuple[str, set[str]]:
    """Return (formatted taxonomy block, set of valid leaf slugs).

    Leaf concepts are level == 1; level-0 rows are parent categories used
    only for grouping. The block is grouped by parent for readability.
    """
    concepts = list(session.execute(select(Concept)).scalars().all())
    by_id = {c.id: c for c in concepts}
    leaves = [c for c in concepts if c.level == 1]

    groups: dict[uuid.UUID | None, list[Concept]] = defaultdict(list)
    for c in leaves:
        groups[c.parent_id].append(c)

    lines = ["TAXONOMY — assign only these concept slugs:", ""]
    for parent_id, kids in groups.items():
        parent = by_id.get(parent_id)
        lines.append(f"## {parent.name if parent else '(uncategorized)'}")
        for c in sorted(kids, key=lambda x: x.slug):
            desc = (c.description or "").strip()
            lines.append(f"- {c.slug}: {c.name}." + (f" {desc}" if desc else ""))
        lines.append("")

    return "\n".join(lines), {c.slug for c in leaves}


def build_book_block(book: Book) -> str:
    desc = (book.description or "").strip() or "(no description available)"
    return (
        "BOOK TO ANNOTATE:\n"
        f"Title: {book.title}\n"
        f"Author: {book.author}\n"
        f"Description: {desc}\n\n"
        "Return the JSON object now."
    )


def parse_annotations(
    text: str, valid_slugs: set[str]
) -> tuple[list[ParsedAnnotation], list[str]]:
    """Tolerant parse of the model's JSON. Drops invalid slugs and any
    strength outside {1.0, 0.5}. Returns (annotations, warnings)."""
    warnings: list[str] = []
    text = (text or "").strip()
    if text.startswith("```"):
        body = text.split("\n")[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        text = "\n".join(body).strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return [], ["no JSON object found in response"]
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        return [], [f"JSON parse error: {e}"]

    out: list[ParsedAnnotation] = []
    for a in obj.get("annotations", []):
        slug = a.get("concept_slug")
        raw_strength = a.get("strength")
        rationale = (a.get("rationale") or "").strip()
        if slug not in valid_slugs:
            warnings.append(f"invalid slug dropped: {slug!r}")
            continue
        try:
            strength = float(raw_strength)
        except (TypeError, ValueError):
            warnings.append(f"bad strength for {slug}: {raw_strength!r}")
            continue
        if strength not in VALID_STRENGTHS:
            warnings.append(f"strength {strength} dropped (not 1.0/0.5): {slug}")
            continue
        out.append(ParsedAnnotation(slug, strength, rationale))
    return out, warnings


def annotate_book(
    client: Anthropic,
    book: Book,
    taxonomy_block: str,
    valid_slugs: set[str],
    model: str,
    max_retries: int = 2,
) -> tuple[list[ParsedAnnotation], list[str]]:
    """One book -> its concept annotations. Taxonomy block is cached across
    calls via cache_control (it's identical for every book)."""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT},
                    {
                        "type": "text",
                        "text": taxonomy_block,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": build_book_block(book)}],
            )
            text = resp.content[0].text if resp.content else ""
            return parse_annotations(text, valid_slugs)
        except APIError as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
            continue
    return [], [f"API error after {max_retries + 1} attempts: {last_err}"]


def resolve_model(name: str) -> str:
    return MODELS.get(name, name)


def load_done_ids(done_path: Path) -> set[str]:
    """Book IDs already processed in a previous (interrupted) run."""
    if not done_path.exists():
        return set()
    return {
        line.strip() for line in done_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch auto-annotate unannotated books (writes CSV only).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sonnet | opus | haiku | raw-id")
    parser.add_argument("--limit", type=int, default=None, help="annotate only the first N (smoke test)")
    parser.add_argument("--output", type=Path, default=OUTPUT_CSV)
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted run (skip books in the .done sidecar)")
    parser.add_argument("--overwrite", action="store_true",
                        help="discard any existing output and start fresh")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        return 2

    done_path = args.output.with_suffix(args.output.suffix + ".done")

    # Refuse to silently clobber a previous run's output.
    if args.output.exists() and not args.resume and not args.overwrite:
        print(f"ERROR: {args.output} already exists.")
        print("Pass --resume to continue an interrupted run, or --overwrite to start fresh.")
        return 2
    if args.overwrite:
        args.output.unlink(missing_ok=True)
        done_path.unlink(missing_ok=True)

    done_ids = load_done_ids(done_path) if args.resume else set()

    model = resolve_model(args.model)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    engine = create_engine(db_url)

    with Session(engine) as session:
        taxonomy_block, valid_slugs = load_taxonomy(session)
        annotated_subq = select(BookConceptAnnotation.book_id).distinct()
        books = list(session.execute(
            select(Book)
            .where(Book.id.not_in(annotated_subq))
            .order_by(Book.id)  # deterministic: makes --limit and --resume reproducible
        ).scalars().all())
        if done_ids:
            before = len(books)
            books = [b for b in books if str(b.id) not in done_ids]
            print(f"Resume: skipping {before - len(books)} already-done books.")
        if args.limit:
            books = books[: args.limit]
        print(f"Annotating {len(books)} unannotated books with {model} ...")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_header = not (args.resume and args.output.exists())
        rows = 0
        csv_mode = "a" if (args.resume and args.output.exists()) else "w"
        with open(args.output, csv_mode, encoding="utf-8", newline="") as f, \
             open(done_path, "a", encoding="utf-8") as done_f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "book_id", "title", "author", "concept_slug",
                    "strength", "rationale", "model", "created_at",
                ])
            for i, book in enumerate(books, 1):
                anns, warns = annotate_book(client, book, taxonomy_block, valid_slugs, model)
                for w in warns:
                    print(f"  [warn] {book.title[:40]}: {w}")
                now = datetime.now(timezone.utc).isoformat()
                for a in anns:
                    writer.writerow([
                        book.id, book.title, book.author, a.concept_slug,
                        a.strength, a.rationale, model, now,
                    ])
                    rows += 1
                # Flush after every book so a crash loses at most one call.
                f.flush()
                done_f.write(f"{book.id}\n")
                done_f.flush()
                print(f"  [{i}/{len(books)}] {book.title[:45]:<45} -> {len(anns)} concepts")

        print(f"\nWrote {rows} annotation rows -> {args.output}")
        print(f"Progress sidecar: {done_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
