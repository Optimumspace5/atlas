"""Track B1 (merge): append the verified must-add books into the curated catalog.

Reads data/must_adds_v1.csv and appends its rows into
data/curated_core_catalog_v2.csv, skipping any whose google_volume_id (or
normalized title) is already present. Idempotent: safe to re-run. Rewrites the
catalog in place with a consistent header.

Usage:
    python scripts/merge_must_adds.py
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

CATALOG = Path("data/curated_core_catalog_v2.csv")
MUSTADDS = Path("data/must_adds_v1.csv")


def norm(t: str) -> str:
    t = (t or "").lower().split(":")[0]
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames or [], list(r)


def main() -> int:
    if not CATALOG.exists() or not MUSTADDS.exists():
        print("ERROR: catalog or must_adds file missing")
        return 2

    fields, catalog = _read(CATALOG)
    _, adds = _read(MUSTADDS)

    have_vid = {(r.get("google_volume_id") or "").strip() for r in catalog if r.get("google_volume_id")}
    have_title = {norm(r.get("title", "")) for r in catalog}

    appended, skipped = [], []
    for r in adds:
        vid = (r.get("google_volume_id") or "").strip()
        nt = norm(r.get("title", ""))
        if (vid and vid in have_vid) or nt in have_title:
            skipped.append(r.get("title", ""))
            continue
        appended.append({k: r.get(k, "") for k in fields})
        if vid:
            have_vid.add(vid)
        have_title.add(nt)

    merged = catalog + appended
    with CATALOG.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(merged)

    print(f"Catalog: {len(catalog)} -> {len(merged)} (+{len(appended)} added, {len(skipped)} skipped)")
    if skipped:
        print("  skipped (already present):")
        for t in skipped:
            print(f"    - {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
