"""Stage 2 of the corpus quality audit: judge each book from fixed evidence.

Reads an evidence JSONL (Stage 1 web evidence by default, or the metadata-only
file via --evidence) + data/corpus_dedup_groups_v1.csv, asks Claude to assign
quality/relevance tiers per the rubric (NO web search -- it reasons over
already-collected evidence), and writes data/corpus_quality_audit_v1.csv.

Re-runnable for cheap: tune the rubric here and re-run without re-searching.
Crash-safe (.done sidecar, append, --resume).

Failures are NEVER silently relabeled: if the model errors or returns
unparseable JSON, the row is written with quality_tier="" and
judge_status set to the failure cause, and the raw response is dumped to
data/corpus_quality_judge_debug_v1.jsonl for inspection.

Usage:
    python scripts/judge_corpus_quality.py --limit 10        # tune on the sample
    python scripts/judge_corpus_quality.py --model fable     # judge all web evidence
    python scripts/judge_corpus_quality.py --resume
    # A4: judge the metadata-only books, appending onto the same audit CSV:
    python scripts/judge_corpus_quality.py \
        --evidence data/corpus_metadata_evidence_v1.jsonl --resume

Requires ANTHROPIC_API_KEY in env.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from anthropic import Anthropic, APIError

EVIDENCE_JSONL = Path("data/corpus_quality_evidence_v1.jsonl")
DEDUP_CSV = Path("data/corpus_dedup_groups_v1.csv")
OUT_CSV = Path("data/corpus_quality_audit_v1.csv")
DEBUG_JSONL = Path("data/corpus_quality_judge_debug_v1.jsonl")

MODELS = {"fable": "claude-fable-5", "sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-8"}
DEFAULT_MODEL = "sonnet"
MAX_TOKENS = 1500
KEEP_DECISION = {"KEEP_A": "keep", "KEEP_B": "keep", "REVIEW_C": "review", "DROP_D": "drop"}

SYSTEM_PROMPT = (
    "You are a corpus-quality judge for Atlas, a knowledge-gap-aware book "
    "recommender for INVESTING, TRADING, FINANCIAL MARKETS, and MACRO. Given "
    "one book's metadata + collected web evidence, assign tiers per the rubric "
    "below. Output ONLY JSON.\n\n"
    "QUALITY TIERS:\n"
    "- KEEP_A: canonical / widely respected / must-keep.\n"
    "- KEEP_B: credible specialist / relevant enough to keep.\n"
    "- REVIEW_C: uncertain; needs a human decision.\n"
    "- DROP_D: low-quality, irrelevant, duplicate, summary/workbook, spammy, "
    "or only weakly related.\n\n"
    "RATING POLICY (use the BOOK-SPECIFIC rating; an author-wide average is "
    "weaker evidence -- note that):\n"
    "- >= 4.20: very strong signal.\n"
    "- 3.75-4.19: acceptable / positive.\n"
    "- 3.50-3.74: mixed; needs stronger author/publisher/relevance evidence.\n"
    "- < 3.50: weak; lean REVIEW_C or DROP_D unless canonical.\n"
    "- no rating: NEUTRAL, not an automatic drop; judge from author, publisher, "
    "reputation, citations, reading-list mentions, and relevance.\n\n"
    "REVIEW-COUNT CONFIDENCE (for 3.75+): 500+ strong; 50-499 moderate; "
    "<50 weak; no rating neutral.\n\n"
    "HARD RED-FLAG RULE: if title/description signals a summary, workbook, "
    "study-guide, 'X books in 1' bundle, 'for beginners', 'get rich' / "
    "'secrets' / 'passive income', or hyperbolic return claims (e.g. '50% to "
    "100%'), DEFAULT to DROP_D -- UNLESS strong evidence (canonical status, "
    "high-volume strong ratings, credible author) clearly overrides.\n\n"
    "CANONICAL OVERRIDE: a book may be KEEP_A even with missing or sub-3.75 "
    "ratings if it is widely recognized, by a credible/foundational author, or "
    "recurs on reputable reading lists -- but you MUST justify why in `reason`.\n\n"
    "RELEVANCE is a SEPARATE axis from quality: a high-quality book outside "
    "Atlas's investing/trading/markets/macro scope (e.g. an international "
    "financial-LAW yearbook) is low-relevance and should not be kept on "
    "relevance grounds.\n\n"
    "Keep `reason` to 2-4 sentences and `red_flags` concise so the JSON is "
    "always complete. Output ONLY this JSON, no prose, no fences:\n"
    "{\n"
    '  "quality_tier": "KEEP_A|KEEP_B|REVIEW_C|DROP_D",\n'
    '  "relevance_tier": "high|medium|low",\n'
    '  "confidence": "high|medium|low",\n'
    '  "avg_rating_best": <float or null>,\n'
    '  "review_count_best": <int or null>,\n'
    '  "rating_source_best": "goodreads|amazon|google|none",\n'
    '  "red_flags": ["..."],\n'
    '  "reason": "<2-4 sentences justifying the tier, citing the evidence>"\n'
    "}"
)


def load_dedup() -> dict:
    groups = {}
    if DEDUP_CSV.exists():
        with DEDUP_CSV.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                groups[r["google_volume_id"]] = (
                    r.get("duplicate_group", ""),
                    r.get("suggested_canonical_title", ""),
                )
    return groups


def build_prompt(ev: dict) -> str:
    lines = []
    for w in ev.get("web_evidence", []):
        lines.append(
            f"- [{w.get('source')}] rating={w.get('rating')} "
            f"count={w.get('review_count')} :: {w.get('snippet')} ({w.get('url')})"
        )
    web = "\n".join(lines) or "(no web evidence found)"
    return (
        f"Title: {ev.get('title')}\n"
        f"Author: {ev.get('author')}\n"
        f"Publisher: {ev.get('publisher')}\n"
        f"Year: {ev.get('publication_year')}\n"
        f"Google Books rating: {ev.get('google_books_rating')} "
        f"(count {ev.get('google_books_ratings_count')})\n"
        f"Existing manual annotations in Atlas: {ev.get('existing_annotation_count')}\n"
        f"Description: {ev.get('description')}\n\n"
        f"WEB EVIDENCE:\n{web}\n\n"
        f"Researcher's evidence summary: {ev.get('evidence_notes')}\n\n"
        "Judge this book. Output ONLY the JSON object."
    )


def parse_json(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        body = text.split("\n")[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        text = "\n".join(body).strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


def judge_one(client, ev, model, max_retries=2) -> dict:
    """Return a result dict with explicit success/failure.

    Keys: ok(bool), data(dict|None), raw(str), stop_reason(str), error(str).
    A failure is never collapsed into a default tier by the caller.
    """
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(ev)}],
            )
            raw = resp.content[0].text if resp.content else ""
            stop = getattr(resp, "stop_reason", "") or ""
            data = parse_json(raw)
            if data is None:
                last_err = f"parse_failed (stop_reason={stop})"
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                return {"ok": False, "data": None, "raw": raw,
                        "stop_reason": stop, "error": last_err}
            return {"ok": True, "data": data, "raw": raw,
                    "stop_reason": stop, "error": ""}
        except APIError as e:
            last_err = f"api_error: {e}"
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
    return {"ok": False, "data": None, "raw": "",
            "stop_reason": "", "error": last_err or "api_failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--evidence", type=Path, default=EVIDENCE_JSONL,
        help="evidence JSONL to judge (default: web evidence; pass "
             "data/corpus_metadata_evidence_v1.jsonl for the A4 metadata pass)",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set"); return 2
    if not args.evidence.exists():
        print(f"ERROR: {args.evidence} not found"); return 2

    model = MODELS.get(args.model, args.model)
    done_path = OUT_CSV.with_suffix(OUT_CSV.suffix + ".done")
    if OUT_CSV.exists() and not args.resume and not args.overwrite:
        print(f"ERROR: {OUT_CSV} exists. Pass --resume or --overwrite."); return 2
    if args.overwrite:
        OUT_CSV.unlink(missing_ok=True)
        done_path.unlink(missing_ok=True)
        DEBUG_JSONL.unlink(missing_ok=True)
    done = set()
    if args.resume and done_path.exists():
        done = {l.strip() for l in done_path.read_text(encoding="utf-8").splitlines() if l.strip()}

    dedup = load_dedup()
    records = [json.loads(l) for l in args.evidence.read_text(encoding="utf-8").splitlines() if l.strip()]
    targets = [r for r in records if str(r.get("corpus_row")) not in done]
    if args.limit:
        targets = targets[: args.limit]

    fieldnames = [
        "corpus_row", "title", "author", "source", "quality_tier",
        "relevance_tier", "confidence", "keep_decision", "judge_status",
        "avg_rating_best", "review_count_best", "rating_source_best",
        "evidence_urls", "reason", "red_flags", "duplicate_group",
        "suggested_canonical_title",
    ]
    write_header = not (args.resume and OUT_CSV.exists())
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    mode = "a" if (args.resume and OUT_CSV.exists()) else "w"
    print(f"Judging {len(targets)} books with {model} (evidence: {args.evidence.name}) ...")
    n = 0
    n_failed = 0
    with OUT_CSV.open(mode, encoding="utf-8", newline="") as f, \
         done_path.open("a", encoding="utf-8") as donef, \
         DEBUG_JSONL.open("a", encoding="utf-8") as debugf:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for i, ev in enumerate(targets, 1):
            res = judge_one(client, ev, model)
            v = res["data"] or {}
            vid = ev.get("google_volume_id", "")
            dup_group, canon_title = dedup.get(vid, ("", ""))
            if res["ok"]:
                status = "ok"
                qt = v.get("quality_tier", "")
                keep = KEEP_DECISION.get(qt, "review")
            else:
                status = res["error"]
                qt = ""                    # never disguise a failure as a tier
                keep = "review"            # a failed judgment must be re-run, not kept/dropped
                n_failed += 1
                debugf.write(json.dumps({
                    "corpus_row": ev.get("corpus_row"),
                    "title": ev.get("title"),
                    "error": res["error"],
                    "stop_reason": res["stop_reason"],
                    "raw": res["raw"],
                }, ensure_ascii=False) + "\n")
                debugf.flush()
            urls = "; ".join(w.get("url", "") for w in ev.get("web_evidence", []) if w.get("url"))
            writer.writerow({
                "corpus_row": ev.get("corpus_row"),
                "title": ev.get("title"),
                "author": ev.get("author"),
                "source": ev.get("source"),
                "quality_tier": qt,
                "relevance_tier": v.get("relevance_tier", ""),
                "confidence": v.get("confidence", ""),
                "keep_decision": keep,
                "judge_status": status,
                "avg_rating_best": v.get("avg_rating_best", ""),
                "review_count_best": v.get("review_count_best", ""),
                "rating_source_best": v.get("rating_source_best", ""),
                "evidence_urls": urls,
                "reason": v.get("reason", ""),
                "red_flags": "; ".join(v.get("red_flags", []) or []),
                "duplicate_group": dup_group,
                "suggested_canonical_title": canon_title,
            })
            f.flush()
            donef.write(f"{ev.get('corpus_row')}\n"); donef.flush()
            n += 1
            flag = "" if res["ok"] else f"   <-- JUDGE FAILED ({status})"
            print(f"  [{i}/{len(targets)}] {str(ev.get('title',''))[:42]:<42} "
                  f"-> {qt or 'FAILED':<8} / rel={v.get('relevance_tier','?')}{flag}")
    print(f"\nWrote {n} audit rows -> {OUT_CSV}")
    if n_failed:
        print(f"WARNING: {n_failed} judgment(s) FAILED -- see {DEBUG_JSONL} "
              f"(raw text + stop_reason). Re-run with --resume after a fix; "
              f"failed rows have judge_status != 'ok' and an empty quality_tier.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
