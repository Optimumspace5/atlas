"""Track B3 (validate): compare grounded annotations to the manual gold set.

For each book in data/grounded_annotations_v1.csv, diff its predicted concepts
against the manual/manual_audit gold in data/annotations_v1.csv and report
precision / recall / F1 + strength agreement (micro, pooled across books) --
the same metrics auto_annotate.py was validated on (P 0.752 / R 0.659 /
F1 0.702 / strength 0.690). It also lists the FP (grounded extra) and FN (gold
missed) concepts per book, because grounded "errors" are often real concepts
the human annotator simply didn't tag -- so read the lists, not just the F1.

Usage:
    python scripts/compare_grounded_vs_gold.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

GROUNDED = Path("data/grounded_annotations_v1.csv")
GOLD = Path("data/annotations_v1.csv")
MANUAL_TYPES = {"manual", "manual_audit"}


def _read(p: Path) -> list[dict]:
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> int:
    grounded = _read(GROUNDED)
    gold_all = _read(GOLD)
    vids = {r["google_volume_id"] for r in grounded if r.get("google_volume_id")}
    title_by_vid = {r["google_volume_id"]: r["title"] for r in grounded}

    g: dict[str, dict[str, float]] = defaultdict(dict)
    for r in grounded:
        g[r["google_volume_id"]][r["concept_slug"]] = _f(r.get("strength"))

    gold: dict[str, dict[str, float]] = defaultdict(dict)
    for r in gold_all:
        if r.get("annotation_type") in MANUAL_TYPES and r.get("google_volume_id") in vids:
            gold[r["google_volume_id"]][r["concept_slug"]] = _f(r.get("strength"))

    tot_tp = tot_fp = tot_fn = 0
    str_match = str_total = 0

    for vid in vids:
        gp, gd = g[vid], gold[vid]
        gp_set, gd_set = set(gp), set(gd)
        tp, fp, fn = gp_set & gd_set, gp_set - gd_set, gd_set - gp_set
        P = len(tp) / len(gp_set) if gp_set else 0.0
        R = len(tp) / len(gd_set) if gd_set else 0.0
        F1 = 2 * P * R / (P + R) if (P + R) else 0.0
        sm = sum(1 for c in tp if gp[c] == gd[c])
        tot_tp += len(tp); tot_fp += len(fp); tot_fn += len(fn)
        str_match += sm; str_total += len(tp)

        print(f"\n=== {title_by_vid[vid][:55]} ===")
        print(f"  gold={len(gd_set)} grounded={len(gp_set)}  "
              f"P={P:.3f} R={R:.3f} F1={F1:.3f}  "
              f"strength agree={sm}/{len(tp)}")
        if fn:
            print(f"  MISSED (gold has, grounded omitted): {', '.join(sorted(fn))}")
        if fp:
            print(f"  EXTRA  (grounded has, gold lacks)  : {', '.join(sorted(fp))}")

    mP = tot_tp / (tot_tp + tot_fp) if (tot_tp + tot_fp) else 0.0
    mR = tot_tp / (tot_tp + tot_fn) if (tot_tp + tot_fn) else 0.0
    mF1 = 2 * mP * mR / (mP + mR) if (mP + mR) else 0.0
    mStr = str_match / str_total if str_total else 0.0

    print("\n" + "=" * 60)
    print(f"MICRO (pooled over {len(vids)} books):")
    print(f"  precision={mP:.3f}  recall={mR:.3f}  F1={mF1:.3f}  strength_agree={mStr:.3f}")
    print(f"  bulk auto_annotate baseline: P 0.752 / R 0.659 / F1 0.702 / strength 0.690")
    verdict = "BEATS" if mF1 >= 0.702 else "BELOW"
    print(f"  -> grounded {verdict} the bulk F1 baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
