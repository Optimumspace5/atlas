"""Track C1: concept coverage report over the curated catalog.

Counts how many catalog books cover each taxonomy concept (and each top-level
category), pooling all annotation sources (manual gold + bulk auto + grounded).
Surfaces the THIN and ZERO-coverage concepts -- the gaps Track C should fill via
targeted candidate books.

Book identity is the normalized title (the only key shared across all three
annotation files; auto_annotations is keyed by DB book_id, not google_volume_id).
The full taxonomy (incl. zero-coverage concepts) comes from the DB.

Inputs (read-only):
  data/curated_core_catalog_v2.csv   (catalog membership; 232 books)
  data/annotations_v1.csv            (manual gold)
  data/auto_annotations_v1.csv       (bulk)
  data/grounded_annotations_v1.csv   (must-adds)
  DB Concept table                   (full leaf list + parent grouping)

Usage:
    python scripts/coverage_report.py            # default thin threshold < 3
    python scripts/coverage_report.py --thin 5
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Concept  # noqa: E402

CATALOG = Path("data/curated_core_catalog_v2.csv")
SOURCES = [
    Path("data/annotations_v1.csv"),
    Path("data/auto_annotations_v1.csv"),
    Path("data/grounded_annotations_v1.csv"),
]


def norm(t: str) -> str:
    t = (t or "").lower().split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thin", type=int, default=3, help="flag concepts with < THIN books")
    args = parser.parse_args()

    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set (needed for the full taxonomy)")
        return 2

    engine = create_engine(db_url)
    with Session(engine) as s:
        concepts = list(s.execute(select(Concept)).scalars().all())
    by_id = {c.id: c for c in concepts}
    leaves = [c for c in concepts if c.level == 1]
    slug_parent = {
        c.slug: (by_id[c.parent_id].name if c.parent_id in by_id else "(uncategorized)")
        for c in leaves
    }
    leaf_slugs = {c.slug for c in leaves}

    cat = _read(CATALOG)
    cat_titles = {norm(r.get("title", "")) for r in cat}
    n_books = len(cat)

    # concept_slug -> set of catalog book identities (normalized title)
    cover: dict[str, set] = defaultdict(set)
    for src in SOURCES:
        for r in _read(src):
            slug = r.get("concept_slug", "")
            nt = norm(r.get("title", ""))
            if slug in leaf_slugs and nt in cat_titles:
                cover[slug].add(nt)

    # parent -> distinct books covering ANY concept in it
    parent_books: dict[str, set] = defaultdict(set)
    parent_leaves: dict[str, list] = defaultdict(list)
    for c in leaves:
        parent = slug_parent[c.slug]
        parent_leaves[parent].append(c.slug)
        parent_books[parent] |= cover.get(c.slug, set())

    print(f"Catalog books: {n_books}   |   leaf concepts: {len(leaves)}\n")
    print("=== TOP-LEVEL CATEGORY COVERAGE (distinct books) ===")
    rows = []
    for parent, leaves_in in parent_leaves.items():
        nb = len(parent_books[parent])
        thin = sum(1 for sl in leaves_in if len(cover.get(sl, set())) < args.thin)
        rows.append((nb, parent, len(leaves_in), thin))
    for nb, parent, nleaf, thin in sorted(rows):
        print(f"  {nb:>3} books | {parent:<46} | {nleaf} leaves, {thin} thin (<{args.thin})")

    print(f"\n=== THINNEST LEAF CONCEPTS (< {args.thin} books, incl. zero) ===")
    leaf_counts = sorted(
        ((len(cover.get(c.slug, set())), c.slug, slug_parent[c.slug]) for c in leaves),
        key=lambda x: (x[0], x[1]),
    )
    for cnt, slug, parent in leaf_counts:
        if cnt < args.thin:
            print(f"  {cnt:>2} | {slug:<46} | {parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
