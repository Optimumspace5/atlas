"""Track B2: grounded annotator for canonical books (manual_grounded).

Unlike auto_annotate.py (which judges from the Google Books blurb only), this
first WEB-SEARCHES each book for what it actually teaches -- table of contents,
chapter topics, substantive reviews -- and assigns taxonomy concepts from that
grounded evidence. It also emits a per-book difficulty tier (1 intro / 2 core /
3 deep) for the Track D roadmaps.

Reuses auto_annotate.py's taxonomy loader + strength rules so the schema and
concept vocabulary match exactly. Output is keyed by google_volume_id (the
books may not be in the DB yet); Convergence maps that to book_id at load time.

Outputs (both crash-safe, --resume):
  data/grounded_annotations_v1.csv   (google_volume_id, ..., concept_slug, strength, rationale, annotation_type=manual_grounded)
  data/book_difficulty_v1.csv        (google_volume_id, title, author, difficulty_tier, difficulty_rationale)

Input CSV needs columns: google_volume_id, title, author, description.
Default input is data/must_adds_v1.csv; pass --input to validate on manual books.

Usage:
    python scripts/annotate_grounded.py                              # annotate must-adds
    python scripts/annotate_grounded.py --input data/validate_manual.csv --overwrite
    python scripts/annotate_grounded.py --resume

Requires DATABASE_URL (taxonomy) and ANTHROPIC_API_KEY in env.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic, APIError
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.auto_annotate import MODELS, VALID_STRENGTHS, load_taxonomy  # noqa: E402

DEFAULT_INPUT = Path("data/must_adds_v1.csv")
GROUNDED_CSV = Path("data/grounded_annotations_v1.csv")
DIFFICULTY_CSV = Path("data/book_difficulty_v1.csv")
DEFAULT_MODEL = "sonnet"
MAX_TOKENS = 2200
VALID_DIFFICULTY = {1, 2, 3}

SYSTEM_PROMPT = (
    "You are a financial-literature cataloguer for Atlas, a knowledge-gap-aware "
    "book recommender. For one book, assign the concepts from a fixed taxonomy "
    "that the book genuinely teaches, AND assign a difficulty tier.\n\n"
    "METHOD: FIRST use web search to find what the book actually covers -- its "
    "table of contents, chapter topics, and substantive reviews. Base every "
    "concept, strength, and the difficulty on that grounded evidence, not on the "
    "short description alone and not on assumptions.\n\n"
    "Rules:\n"
    "1. Assign a concept ONLY if the book actively teaches or substantially "
    "covers it. When in doubt, OMIT it. Precision matters more than recall.\n"
    "2. Use ONLY concept slugs from the provided taxonomy. Never invent a slug.\n"
    "3. Strength is exactly one of:\n"
    "   - 1.0  a major, recurring topic the book actively teaches\n"
    "   - 0.5  a real but secondary topic\n"
    "   Omit tangential mentions entirely.\n"
    "4. difficulty_tier is exactly one of:\n"
    "   - 1  introductory / beginner entry point (few prerequisites)\n"
    "   - 2  core / intermediate (assumes basic familiarity)\n"
    "   - 3  advanced / deep specialist (assumes substantial background)\n"
    "5. Output ONLY a JSON object, no prose and no markdown fences:\n"
    '   {"annotations": [{"concept_slug": "...", "strength": 1.0, '
    '"rationale": "<one sentence grounded in the web evidence>"}], '
    '"difficulty_tier": 2, "difficulty_rationale": "<one sentence>"}\n'
    '   If no concepts apply, return an empty annotations list.'
)


def build_prompt(book: dict) -> str:
    desc = (book.get("description") or "").strip() or "(no description available)"
    return (
        "BOOK TO ANNOTATE:\n"
        f"Title: {book.get('title','')}\n"
        f"Author: {book.get('author','')}\n"
        f"Description: {desc}\n\n"
        "Search the web for what this book actually teaches, then return the "
        "JSON object."
    )


def response_text(resp) -> str:
    return "\n".join(
        b.text for b in (resp.content or [])
        if getattr(b, "type", None) == "text" and getattr(b, "text", None)
    )


def parse_result(text: str, valid_slugs: set[str]) -> tuple[list[dict], int | None, str, list[str]]:
    """Return (annotations, difficulty_tier, difficulty_rationale, warnings)."""
    warnings: list[str] = []
    text = (text or "").strip()
    if text.startswith("```"):
        body = text.split("\n")[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        text = "\n".join(body).strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return [], None, "", ["no JSON object found"]
    try:
        obj = json.loads(text[s:e + 1])
    except json.JSONDecodeError as ex:
        return [], None, "", [f"JSON parse error: {ex}"]

    anns: list[dict] = []
    for a in obj.get("annotations", []):
        slug = a.get("concept_slug")
        rationale = (a.get("rationale") or "").strip()
        if slug not in valid_slugs:
            warnings.append(f"invalid slug dropped: {slug!r}")
            continue
        try:
            strength = float(a.get("strength"))
        except (TypeError, ValueError):
            warnings.append(f"bad strength for {slug}")
            continue
        if strength not in VALID_STRENGTHS:
            warnings.append(f"strength {strength} dropped: {slug}")
            continue
        anns.append({"concept_slug": slug, "strength": strength, "rationale": rationale})

    diff = obj.get("difficulty_tier")
    try:
        diff = int(diff)
    except (TypeError, ValueError):
        diff = None
    if diff not in VALID_DIFFICULTY:
        warnings.append(f"invalid difficulty_tier: {obj.get('difficulty_tier')!r}")
        diff = None
    diff_rationale = (obj.get("difficulty_rationale") or "").strip()
    return anns, diff, diff_rationale, warnings


def annotate_one(client, book, taxonomy_block, valid_slugs, model, max_uses, max_retries=2):
    last = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT},
                    {"type": "text", "text": taxonomy_block, "cache_control": {"type": "ephemeral"}},
                ],
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
                messages=[{"role": "user", "content": build_prompt(book)}],
            )
            return parse_result(response_text(resp), valid_slugs)
        except APIError as e:
            last = e
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
    return [], None, "", [f"API error after retries: {last}"]


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-uses", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    load_dotenv()  # pull DATABASE_URL + ANTHROPIC_API_KEY from .env if present
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set (needed for the taxonomy)"); return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set"); return 2
    if not args.input.exists():
        print(f"ERROR: {args.input} not found"); return 2

    model = MODELS.get(args.model, args.model)
    done_path = GROUNDED_CSV.with_suffix(GROUNDED_CSV.suffix + ".done")
    if GROUNDED_CSV.exists() and not args.resume and not args.overwrite:
        print(f"ERROR: {GROUNDED_CSV} exists. Pass --resume or --overwrite."); return 2
    if args.overwrite:
        for p in (GROUNDED_CSV, DIFFICULTY_CSV, done_path):
            p.unlink(missing_ok=True)
    done = load_done(done_path) if args.resume else set()

    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        books = [b for b in csv.DictReader(f)
                 if (b.get("google_volume_id") or "").strip() not in done]
    if args.limit:
        books = books[: args.limit]

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    engine = create_engine(db_url)
    with Session(engine) as session:
        taxonomy_block, valid_slugs = load_taxonomy(session)

    ann_fields = ["google_volume_id", "title", "author", "concept_slug",
                  "strength", "rationale", "annotation_type", "model", "created_at"]
    diff_fields = ["google_volume_id", "title", "author", "difficulty_tier",
                   "difficulty_rationale", "model", "created_at"]
    resume_append = args.resume and GROUNDED_CSV.exists()
    ann_mode = "a" if resume_append else "w"

    print(f"Grounded-annotating {len(books)} books with {model} (input: {args.input.name}) ...")
    total_ann = 0
    with GROUNDED_CSV.open(ann_mode, encoding="utf-8", newline="") as af, \
         DIFFICULTY_CSV.open(ann_mode, encoding="utf-8", newline="") as df, \
         done_path.open("a", encoding="utf-8") as donef:
        aw = csv.DictWriter(af, fieldnames=ann_fields)
        dw = csv.DictWriter(df, fieldnames=diff_fields)
        if not resume_append:
            aw.writeheader(); dw.writeheader()
        for i, book in enumerate(books, 1):
            vid = (book.get("google_volume_id") or "").strip()
            anns, diff, diff_rat, warns = annotate_one(
                client, book, taxonomy_block, valid_slugs, model, args.max_uses)
            for w in warns:
                print(f"    [warn] {book.get('title','')[:36]}: {w}")
            now = datetime.now(timezone.utc).isoformat()
            for a in anns:
                aw.writerow({
                    "google_volume_id": vid,
                    "title": book.get("title", ""),
                    "author": book.get("author", ""),
                    "concept_slug": a["concept_slug"],
                    "strength": a["strength"],
                    "rationale": a["rationale"],
                    "annotation_type": "manual_grounded",
                    "model": model,
                    "created_at": now,
                })
                total_ann += 1
            dw.writerow({
                "google_volume_id": vid,
                "title": book.get("title", ""),
                "author": book.get("author", ""),
                "difficulty_tier": diff if diff is not None else "",
                "difficulty_rationale": diff_rat,
                "model": model,
                "created_at": now,
            })
            af.flush(); df.flush()
            donef.write(f"{vid}\n"); donef.flush()
            print(f"  [{i}/{len(books)}] {book.get('title','')[:40]:<40} "
                  f"-> {len(anns)} concepts, difficulty={diff}")

    print(f"\nWrote {total_ann} grounded annotations -> {GROUNDED_CSV}")
    print(f"Difficulty tiers -> {DIFFICULTY_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
