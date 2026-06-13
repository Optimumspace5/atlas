"""Track D1: generate the two learning roadmaps (investor + trader).

Builds an ordered 10-book "journey" per role from a TRUSTED candidate pool only:
books with manual/manual_audit gold annotations + the 9 grounded must-adds
(NOT the bulk auto-annotated books -- their tags are less reliable). The LLM
selects + orders from that pool under hard rules; you review/edit the result.

Selection rules enforced in the prompt:
  - Pinned anchors at the top: investor rungs 1-2 = Housel then Bogle;
    trader rung 1 = Murphy (Technical Analysis of the Financial Markets).
  - Front-load popular / accessible books (review counts shown where known).
  - Each successive rung must introduce concepts earlier rungs didn't
    (progressive coverage -- different concepts at each step).
  - Foundational -> advanced overall; sensible reading order.
  - 10 books per path, chosen ONLY from the provided pool.

Inputs (read-only):
  data/curated_core_catalog_v2.csv   (title/author + curated membership)
  data/annotations_v1.csv            (manual gold concepts -> pool + concepts)
  data/grounded_annotations_v1.csv   (must-add concepts -> pool + concepts)
  data/book_difficulty_v1.csv        (difficulty hint for the 9 must-adds)
  data/corpus_quality_audit_v1.csv   (review_count/rating popularity signal)
Output:
  data/roadmaps_v1.json

Requires ANTHROPIC_API_KEY (.env auto-loaded).

Usage:
    python scripts/build_roadmaps.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

CATALOG = Path("data/curated_core_catalog_v2.csv")
MANUAL = Path("data/annotations_v1.csv")
GROUNDED = Path("data/grounded_annotations_v1.csv")
DIFFICULTY = Path("data/book_difficulty_v1.csv")
AUDIT = Path("data/corpus_quality_audit_v1.csv")
OUT = Path("data/roadmaps_v1.json")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 3500
MANUAL_TYPES = {"manual", "manual_audit"}

# (title-substring, author-substring) for the pinned starting rungs.
ANCHORS = {
    "investor": [("psychology of money", "housel"), ("common sense investing", "bogle")],
    "trader": [("technical analysis of the financial markets", "murphy")],
}

SYSTEM_PROMPT = (
    "You are designing two ordered learning roadmaps for Atlas, a book "
    "recommender for investing and trading. One roadmap is for a LONG-TERM "
    "INVESTOR, one for a TRADER. Each is a 10-book journey a reader follows "
    "top to bottom.\n\n"
    "Select books ONLY from the provided candidate pool (use the exact titles "
    "given). Follow these rules strictly:\n"
    "1. PINNED ANCHORS occupy the first rung(s) exactly as specified -- do not "
    "move or replace them.\n"
    "2. FRONT-LOAD popularity + accessibility: the earliest non-anchor rungs "
    "should be the most popular / well-reviewed / approachable books (higher "
    "review counts, gentler difficulty).\n"
    "3. PROGRESSIVE CONCEPT COVERAGE: order so each successive rung introduces "
    "concepts the earlier rungs did NOT cover. The journey should broaden the "
    "reader across different concepts, not repeat the same ones.\n"
    "4. FOUNDATIONAL -> ADVANCED overall: a sensible reading order where each "
    "book builds on what came before; deep/abstract books (e.g. Antifragile) "
    "belong later, not early.\n"
    "5. Exactly 10 rungs per path. A book may appear on BOTH paths only if it "
    "is genuinely foundational to both (e.g. a risk/psychology classic); "
    "otherwise keep the two paths distinct.\n\n"
    "Output ONLY this JSON, no prose or fences:\n"
    "{\n"
    '  "investor": [{"rung": 1, "title": "<exact title>", "author": "...", '
    '"new_concepts": ["concept it adds that earlier rungs lacked"], '
    '"why": "<one sentence on why it sits at this rung>"}],\n'
    '  "trader": [ ...same shape, 10 rungs... ]\n'
    "}"
)


def norm(t: str) -> str:
    t = (t or "").lower().split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def is_anchor(title: str, author: str, role: str) -> bool:
    t, a = norm(title), (author or "").lower()
    return any(ts in t and as_ in a for ts, as_ in ANCHORS[role])


def main() -> int:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        return 2

    catalog = {(r.get("google_volume_id") or "").strip(): r
               for r in _read(CATALOG) if r.get("google_volume_id")}

    # concepts per book from the TRUSTED sources only
    concepts: dict[str, set] = defaultdict(set)
    for r in _read(MANUAL):
        if r.get("annotation_type") in MANUAL_TYPES:
            vid = (r.get("google_volume_id") or "").strip()
            if vid:
                concepts[vid].add(r.get("concept_slug", ""))
    for r in _read(GROUNDED):
        vid = (r.get("google_volume_id") or "").strip()
        if vid:
            concepts[vid].add(r.get("concept_slug", ""))

    difficulty = {(r.get("google_volume_id") or "").strip(): r.get("difficulty_tier", "")
                  for r in _read(DIFFICULTY)}
    reviews = {}
    for r in _read(AUDIT):
        reviews[norm(r.get("title", ""))] = (
            r.get("review_count_best", ""), r.get("avg_rating_best", ""))

    # candidate pool = trusted-annotated books that are in the curated catalog
    pool = []
    for vid, slugs in concepts.items():
        cat = catalog.get(vid)
        if not cat:
            continue
        title, author = cat.get("title", ""), cat.get("author", "")
        rc, rating = reviews.get(norm(title), ("", ""))
        roles = [role for role in ANCHORS if is_anchor(title, author, role)]
        pool.append({
            "vid": vid, "title": title, "author": author,
            "concepts": sorted(s for s in slugs if s),
            "difficulty": difficulty.get(vid, ""),
            "review_count": rc, "rating": rating,
            "anchor_for": roles,
        })

    # build the candidate block
    lines = []
    for b in sorted(pool, key=lambda x: x["title"]):
        meta = []
        if b["review_count"]:
            meta.append(f"reviews={b['review_count']}")
        if b["rating"]:
            meta.append(f"rating={b['rating']}")
        if b["difficulty"]:
            meta.append(f"difficulty={b['difficulty']}")
        meta_s = (" [" + ", ".join(meta) + "]") if meta else ""
        lines.append(f"- {b['title']} — {b['author']}{meta_s}\n    concepts: {', '.join(b['concepts'])}")
    pool_block = "\n".join(lines)

    anchor_desc = (
        "PINNED ANCHORS (use these exact books at the top rungs):\n"
        "  investor rung 1: The Psychology of Money (Morgan Housel)\n"
        "  investor rung 2: The Little Book of Common Sense Investing (John C. Bogle)\n"
        "  trader   rung 1: Technical Analysis of the Financial Markets (John J. Murphy)\n"
    )
    user_msg = (
        f"{anchor_desc}\n"
        f"CANDIDATE POOL ({len(pool)} books — pick ONLY from these):\n\n{pool_block}\n\n"
        "Design the two 10-book roadmaps now. Output ONLY the JSON object."
    )

    print(f"Candidate pool: {len(pool)} trusted books. Asking {MODEL} for two roadmaps ...")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text if resp.content else ""
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        print("ERROR: no JSON in response:\n", text[:500])
        return 1
    data = json.loads(text[s:e + 1])

    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for role in ("investor", "trader"):
        print(f"\n=== {role.upper()} PATH ===")
        for rung in data.get(role, []):
            nc = ", ".join(rung.get("new_concepts", []))
            print(f"  {rung.get('rung'):>2}. {rung.get('title','')[:46]:<46} | +{nc}")
    print(f"\nWrote -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
