"""Stage 1 of the corpus quality audit: collect web evidence per book.

For each book in data/corpus_merged_v1.csv, asks Claude (with the
server-side web_search tool) for reputation evidence — Goodreads/Amazon
ratings + review counts, reputable reading-list mentions, author/publisher
reputation, and red flags — and writes one JSON evidence object per book
to data/corpus_quality_evidence_v1.jsonl.

Stage 1 of a two-stage pipeline: evidence is collected ONCE here, then
judged separately (judge_corpus_quality.py) so the rubric can be re-tuned
without re-paying for web searches.

PREREQUISITE: web_search must be enabled for your Anthropic account/Console,
or every call errors. Test with --limit 10 first.

Ratings may legitimately be null — a missing rating is neutral; NEVER
fabricate one. Append-only; no mutation of corpus or annotation files.

Usage:
    python scripts/collect_corpus_evidence.py --limit 10        # dry run
    python scripts/collect_corpus_evidence.py                   # full 468
    python scripts/collect_corpus_evidence.py --resume          # continue
    python scripts/collect_corpus_evidence.py --overwrite       # fresh start
    python scripts/collect_corpus_evidence.py --model fable     # try Fable 5

Requires ANTHROPIC_API_KEY in env.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic, APIError

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
ANNOTATIONS_CSV = Path("data/annotations_v1.csv")
OUT_JSONL = Path("data/corpus_quality_evidence_v1.jsonl")
DESC_LIMIT = 600

MODELS = {
    "fable": "claude-fable-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
}
DEFAULT_MODEL = "sonnet"
MAX_TOKENS = 2500

SYSTEM_PROMPT = (
    "You are a corpus-quality research assistant for Atlas, a knowledge-"
    "gap-aware book recommender focused on investing, trading, and financial "
    "markets. Given one book's metadata, use the web_search tool to gather "
    "REPUTATION EVIDENCE, then output a single JSON object summarizing it.\n\n"
    "Search for, in priority order:\n"
    "1. Goodreads rating and number of ratings.\n"
    "2. Amazon star rating and review count.\n"
    "3. Mentions on reputable investing/trading/finance reading lists or by "
    "credible reviewers.\n"
    "4. Author and publisher reputation / credentials.\n"
    "5. Red flags: a summary/workbook/study-guide of another book; a low-"
    "content 'get rich' / 'secrets' / 'passive income' / 'for beginners' "
    "product; a fake-looking title or author; an irrelevant category; or a "
    "duplicate edition.\n\n"
    "Rules:\n"
    "- Use at most a few targeted searches (title + author + "
    "'goodreads'/'amazon'/'review').\n"
    "- Report a rating ONLY if you actually find it in a source; otherwise "
    "use null. NEVER invent or estimate a rating.\n"
    "- Every web_evidence item must include the source URL.\n"
    "- Output ONLY the JSON object — no prose, no markdown fences:\n"
    "{\n"
    '  "web_evidence": [\n'
    '    {"source": "goodreads|amazon|wikipedia|reading_list|other",\n'
    '     "title": "<page title>", "url": "<url>",\n'
    '     "snippet": "<short quote of the relevant text>",\n'
    '     "rating": <float or null>, "review_count": <int or null>}\n'
    "  ],\n"
    '  "evidence_confidence": "high|medium|low",\n'
    '  "evidence_notes": "<1-3 sentences on quality, relevance, red flags>"\n'
    "}"
)


def load_annotation_counts() -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    if not ANNOTATIONS_CSV.exists():
        return counts
    with ANNOTATIONS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            vid = (row.get("google_volume_id") or "").strip()
            if vid:
                counts[vid] += 1
    return counts


def build_user_prompt(row: dict, ann_count: int) -> str:
    desc = (row.get("description") or "").strip()
    if len(desc) > DESC_LIMIT:
        desc = desc[:DESC_LIMIT] + "..."
    g_rating = (row.get("avg_rating") or "").strip() or "none in Google Books"
    g_count = (row.get("ratings_count") or "").strip() or "none"
    return (
        "BOOK TO RESEARCH:\n"
        f"Title: {row.get('title','')}\n"
        f"Subtitle: {row.get('subtitle','')}\n"
        f"Author: {row.get('author','')}\n"
        f"Publisher: {row.get('publisher','')}\n"
        f"Year: {row.get('publication_year','')}\n"
        f"ISBN-13: {row.get('isbn_13','')}\n"
        f"Categories: {row.get('categories','')}\n"
        f"Google Books rating: {g_rating} (count: {g_count})\n"
        f"Existing manual annotations in Atlas: {ann_count}\n"
        f"Description: {desc}\n\n"
        "Search the web for reputation evidence and output the JSON object."
    )


def extract_text(response) -> str:
    return "\n".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )


def parse_evidence(text: str) -> tuple[dict | None, str]:
    text = (text or "").strip()
    if text.startswith("```"):
        body = text.split("\n")[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        text = "\n".join(body).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None, "no JSON object found"
    try:
        return json.loads(text[start:end + 1]), ""
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"


def collect_one(client, row, ann_count, model, max_uses, max_retries=2):
    prompt = build_user_prompt(row, ann_count)
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": max_uses,
                }],
            )
            return parse_evidence(extract_text(resp))
        except APIError as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(3 * (attempt + 1))
    return None, f"API error after retries: {last_err}"


def to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect corpus quality web evidence.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-uses", type=int, default=4, help="web searches per book")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        return 2
    if not CORPUS_CSV.exists():
        print(f"ERROR: {CORPUS_CSV} not found")
        return 2

    model = MODELS.get(args.model, args.model)
    done_path = OUT_JSONL.with_suffix(OUT_JSONL.suffix + ".done")

    if OUT_JSONL.exists() and not args.resume and not args.overwrite:
        print(f"ERROR: {OUT_JSONL} exists. Pass --resume or --overwrite.")
        return 2
    if args.overwrite:
        OUT_JSONL.unlink(missing_ok=True)
        done_path.unlink(missing_ok=True)

    done_ids = set()
    if args.resume and done_path.exists():
        done_ids = {l.strip() for l in done_path.read_text(encoding="utf-8").splitlines() if l.strip()}

    ann_counts = load_annotation_counts()
    with CORPUS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    targets = []
    for i, row in enumerate(rows, start=1):
        row["_corpus_row"] = i
        if (row.get("google_volume_id") or "").strip() in done_ids:
            continue
        targets.append(row)
    if args.limit:
        targets = targets[: args.limit]

    print(f"Collecting evidence for {len(targets)} books with {model} "
          f"(<= {args.max_uses} searches each) ...")

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    written = 0
    mode = "a" if (args.resume and OUT_JSONL.exists()) else "w"
    with OUT_JSONL.open(mode, encoding="utf-8") as out, \
         done_path.open("a", encoding="utf-8") as done_f:
        for i, row in enumerate(targets, 1):
            vid = (row.get("google_volume_id") or "").strip()
            ann = ann_counts.get(vid, 0)
            evidence, warn = collect_one(client, row, ann, model, args.max_uses)
            web_evidence, conf, notes = [], "low", (warn or "")
            if evidence:
                for item in (evidence.get("web_evidence") or []):
                    web_evidence.append({
                        "source": item.get("source"),
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "snippet": item.get("snippet"),
                        "rating": to_float(item.get("rating")),
                        "review_count": to_int(item.get("review_count")),
                    })
                conf = evidence.get("evidence_confidence", "low")
                notes = evidence.get("evidence_notes", "")
            record = {
                "corpus_row": row["_corpus_row"],
                "google_volume_id": vid,
                "title": row.get("title", ""),
                "author": row.get("author", ""),
                "publisher": row.get("publisher", ""),
                "publication_year": row.get("publication_year", ""),
                "isbn_13": row.get("isbn_13", ""),
                "source": row.get("source", ""),
                "description": (row.get("description", "") or "")[:DESC_LIMIT],
                "existing_annotation_count": ann,
                "google_books_rating": to_float(row.get("avg_rating")),
                "google_books_ratings_count": to_int(row.get("ratings_count")),
                "web_evidence": web_evidence,
                "evidence_confidence": conf,
                "evidence_notes": notes,
                "searched_at": datetime.now(timezone.utc).isoformat(),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            done_f.write(f"{vid}\n")
            done_f.flush()
            written += 1
            flag = "" if web_evidence else "  [no web evidence]"
            print(f"  [{i}/{len(targets)}] {row.get('title','')[:46]:<46} "
                  f"-> {len(web_evidence)} sources, conf={conf}{flag}")

    print(f"\nWrote {written} evidence records -> {OUT_JSONL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
