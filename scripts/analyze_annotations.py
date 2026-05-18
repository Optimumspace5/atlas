"""Analyze taxonomy leaf concept coverage from annotations_v1.csv.

For each of the 48 leaves in the taxonomy, this script reports how many
unique books have at least one human annotation against that leaf, with
strength-weighted aggregation. Output:

- stdout: sparse-first ranked report (action-oriented)
- data/annotation_coverage_v1.csv: full per-leaf metrics

Only human annotations (annotation_type in {'manual', 'manual_audit'}) are
counted. Future model-generated annotations are excluded.

Run from repo root:
    python scripts/analyze_annotations.py
"""

# standard library
import csv
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# local — reuse annotate.py's taxonomy parser
sys.path.insert(0, str(Path(__file__).parent))
from annotate import load_taxonomy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ANNOTATIONS_CSV = Path("data/annotations_v1.csv")
TAXONOMY_YAML = Path("data/taxonomy_v0.1.yaml")
COVERAGE_CSV = Path("data/annotation_coverage_v1.csv")

# Only human annotation types count toward coverage in v1
INCLUDED_ANNOTATION_TYPES: set[str] = {"manual", "manual_audit"}

# Coverage classification thresholds (locked per design)
#   0 books        → empty
#   1 book         → singleton
#   2 books        → sparse
#   3-5 books      → adequate
#   6+ books       → well_covered
THRESHOLD_WELL_COVERED = 6
THRESHOLD_ADEQUATE = 3

# Output CSV column contract (locked)
COVERAGE_COLUMNS: list[str] = [
    "parent_slug",
    "parent_name",
    "concept_slug",
    "concept_name",
    "book_count",
    "confirmed_count",
    "weak_count",
    "conditional_count",
    "weighted_coverage",
    "coverage_status",
    "sample_book_titles",
]

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_annotations(path: Path) -> list[dict[str, Any]]:
    """Read annotations_v1.csv, filtering to human-authored types only."""
    if not path.exists():
        raise FileNotFoundError(f"Annotations file not found: {path}")

    included: list[dict[str, Any]] = []
    excluded_count = 0

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            cleaned = {k: (v or "").strip() for k, v in row.items()}
            if cleaned.get("annotation_type") in INCLUDED_ANNOTATION_TYPES:
                included.append(cleaned)
            else:
                excluded_count += 1

    log.info(
        "Loaded %d annotations (excluded %d non-human types)",
        len(included), excluded_count,
    )
    return included

def validate_annotation_slugs(
    annotations: list[dict[str, Any]],
    taxonomy: dict[str, Any],
) -> None:
    """Fail loudly if any annotation references an unknown leaf slug."""
    valid_slugs = set(taxonomy["leaves_by_slug"].keys())
    bad_slugs: dict[str, int] = defaultdict(int)

    for annotation in annotations:
        slug = annotation.get("concept_slug", "")
        if slug not in valid_slugs:
            bad_slugs[slug] += 1

    if bad_slugs:
        log.error("Found %d annotations with unknown leaf slugs:", sum(bad_slugs.values()))
        for slug, count in sorted(bad_slugs.items(), key=lambda x: -x[1]):
            log.error("  %r: %d annotations", slug, count)
        raise RuntimeError(
            "Annotation slugs don't match taxonomy. Fix annotations_v1.csv or taxonomy_v0.1.yaml."
        )

    log.info("Validated %d annotations against %d taxonomy leaves", len(annotations), len(valid_slugs))

# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_by_leaf(
    annotations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group annotations by concept_slug; compute per-leaf metrics."""
    by_leaf: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "annotations": [],
        "books": set(),
        "confirmed_count": 0,
        "weak_count": 0,
        "conditional_count": 0,
        "weighted_coverage": 0.0,
    })

    for annotation in annotations:
        slug = annotation.get("concept_slug", "")
        if not slug:
            continue

        try:
            strength = float(annotation.get("strength", "") or "0")
        except ValueError:
            log.warning("Skipping annotation with invalid strength: %r",
                        annotation.get("strength"))
            continue

        book_key = annotation.get("book_key", "")
        if book_key and book_key in by_leaf[slug]["books"]:
            log.warning("Duplicate (book, leaf) pair: %s + %s — counting once",
                        book_key, slug)
            continue

        by_leaf[slug]["books"].add(book_key)
        by_leaf[slug]["annotations"].append(annotation)
        by_leaf[slug]["weighted_coverage"] += strength

        if strength == 1.0:
            by_leaf[slug]["confirmed_count"] += 1
        elif strength == 0.5:
            by_leaf[slug]["weak_count"] += 1
        elif strength == 0.3:
            by_leaf[slug]["conditional_count"] += 1

    return dict(by_leaf)

def _get_sample_titles(
    leaf_annotations: list[dict[str, Any]],
    limit: int = 3,
) -> str:
    """Return top N titles for a leaf, sorted by strength desc then alphabetical."""
    sorted_ann = sorted(
        leaf_annotations,
        key=lambda a: (
            -float(a.get("strength", "") or "0"),
            (a.get("title", "") or "").lower(),
        ),
    )
    titles = [(a.get("title", "") or "?") for a in sorted_ann[:limit]]
    return " | ".join(titles)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify_leaves(
    taxonomy: dict[str, Any],
    by_leaf: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a row per taxonomy leaf with coverage metrics and status."""
    rows: list[dict[str, Any]] = []

    empty_metrics = {
        "annotations": [],
        "books": set(),
        "confirmed_count": 0,
        "weak_count": 0,
        "conditional_count": 0,
        "weighted_coverage": 0.0,
    }

    for leaf in taxonomy["leaves"]:
        slug = leaf["slug"]
        metrics = by_leaf.get(slug, empty_metrics)
        book_count = len(metrics["books"])

        if book_count >= THRESHOLD_WELL_COVERED:
            status = "well_covered"
        elif book_count >= THRESHOLD_ADEQUATE:
            status = "adequate"
        elif book_count == 2:
            status = "sparse"
        elif book_count == 1:
            status = "singleton"
        else:
            status = "empty"

        rows.append({
            "parent_slug": leaf["parent_slug"],
            "parent_name": leaf["parent_name"],
            "concept_slug": slug,
            "concept_name": leaf["name"],
            "book_count": book_count,
            "confirmed_count": metrics["confirmed_count"],
            "weak_count": metrics["weak_count"],
            "conditional_count": metrics["conditional_count"],
            "weighted_coverage": round(metrics["weighted_coverage"], 1),
            "coverage_status": status,
            "sample_book_titles": _get_sample_titles(metrics["annotations"]),
        })

    return rows

# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------
def write_coverage_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write classified leaves to CSV atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with tmp_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=COVERAGE_COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            sanitized = {
                column: "" if row.get(column) is None else row.get(column)
                for column in COVERAGE_COLUMNS
            }
            writer.writerow(sanitized)

    os.replace(tmp_path, path)
    log.info("Wrote %d rows to %s", len(rows), path)

# ---------------------------------------------------------------------------
# Stdout report
# ---------------------------------------------------------------------------
def print_sparse_first_report(rows: list[dict[str, Any]]) -> None:
    """Print coverage report grouped by status, sparse first."""
    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_status[row["coverage_status"]].append(row)

    order = ["empty", "singleton", "sparse", "adequate", "well_covered"]
    headers = {
        "empty": "EMPTY (0 books — critical gap)",
        "singleton": "SINGLETON (1 book — single point of failure)",
        "sparse": "SPARSE (2 books — below threshold)",
        "adequate": "ADEQUATE (3-5 books — minimum met)",
        "well_covered": "WELL-COVERED (6+ books)",
    }

    print()
    print("=" * 80)
    print("ANNOTATION COVERAGE REPORT")
    print("=" * 80)

    for status in order:
        leaves = by_status.get(status, [])
        print()
        print(f"{headers[status]}: {len(leaves)} leaves")
        if not leaves:
            continue

        # within-status sort: most-actionable first
        leaves.sort(key=lambda r: (
            r["book_count"],
            r["weighted_coverage"],
            r["concept_slug"],
        ))

        print("-" * 80)
        for leaf in leaves:
            print(f"  {leaf['concept_slug']} ({leaf['parent_slug']})")
            print(f"    {leaf['book_count']} books, weighted={leaf['weighted_coverage']}")
            if leaf["sample_book_titles"]:
                print(f"    sample: {leaf['sample_book_titles']}")
    print()

def main() -> int:
    """Run the coverage analysis end-to-end."""
    log.info("Loading annotations from %s", ANNOTATIONS_CSV)
    annotations = load_annotations(ANNOTATIONS_CSV)

    log.info("Loading taxonomy from %s", TAXONOMY_YAML)
    taxonomy = load_taxonomy(TAXONOMY_YAML)

    validate_annotation_slugs(annotations, taxonomy)

    by_leaf = aggregate_by_leaf(annotations)
    rows = classify_leaves(taxonomy, by_leaf)
    write_coverage_csv(rows, COVERAGE_CSV)

    print_sparse_first_report(rows)

    log.info("Done. Coverage CSV: %s", COVERAGE_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
