"""Load the taxonomy YAML into the concepts table.

Reads data/taxonomy_v1.0.yaml and inserts a row for each parent category
(level=0) and each leaf concept (level=1) via the SQLAlchemy ORM.
Idempotent: re-running the script does not create duplicates.

Loading order matters because concepts.parent_id is a FK back to concepts.id:
parents must be inserted (and flushed, so the DB assigns a UUID) BEFORE the
leaves that reference them.

Idempotency strategy (per concept):
    - Look up by slug (concepts.slug is UNIQUE).
    - If a row exists, validate that its level and parent_id match what the
      YAML expects. Mismatch -> hard error, abort the run, no partial inserts.
    - If name or description differ between DB and YAML, log a WARN but
      do not overwrite (conflict action is DO NOTHING).
    - If no row exists, insert.

Usage:
    python scripts/load_concepts.py
    python scripts/load_concepts.py --yaml data/taxonomy_v1.1.yaml
    python scripts/load_concepts.py --dry-run

Requires DATABASE_URL in env, e.g.:
    $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Concept  # noqa: E402


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DEFAULT_YAML = REPO_ROOT / "data" / "taxonomy_v1.0.yaml"

PREFIX_ADD = "[ADD ]"
PREFIX_SKIP = "[SKIP]"
PREFIX_WARN = "[WARN]"
PREFIX_FAIL = "[FAIL]"


class StructuralMismatchError(Exception):
    """Raised when an existing concept's level or parent_id disagrees with YAML.

    Structural fields are immutable identity; a mismatch means either the YAML
    has been edited incompatibly or the DB has been hand-modified. Either way,
    the loader aborts rather than producing a corrupt mix.
    """


# -----------------------------------------------------------------------------
# YAML parsing
# -----------------------------------------------------------------------------
def load_yaml(path: Path) -> list[dict]:
    """Parse the taxonomy YAML and return the categories list.

    Validates that the file has a top-level `categories` key whose value is
    a non-empty list. Does not validate per-category shape — that happens
    naturally when we try to read .get('id'), etc., during processing.
    """
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "categories" not in data:
        raise ValueError(f"{path}: missing top-level 'categories' key")
    categories = data["categories"]
    if not isinstance(categories, list) or not categories:
        raise ValueError(f"{path}: 'categories' must be a non-empty list")
    return categories


def normalize_description(value: str | None) -> str | None:
    """YAML folded scalars (>) often add a trailing newline; strip it for DB."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


# -----------------------------------------------------------------------------
# Per-concept processing
# -----------------------------------------------------------------------------
def process_concept(
    session: Session,
    *,
    slug: str,
    name: str,
    description: str | None,
    expected_level: int,
    expected_parent_id: uuid.UUID | None,
    stats: dict[str, int],
) -> uuid.UUID:
    """Process one concept (parent or leaf).

    Returns the DB UUID of the concept (existing or newly inserted) so the
    caller can use it as parent_id for child concepts.

    Raises StructuralMismatchError if an existing concept disagrees on level
    or parent_id.
    """
    existing = session.scalar(select(Concept).where(Concept.slug == slug))

    if existing is not None:
        # Hard structural checks
        if existing.level != expected_level:
            print(f"{PREFIX_FAIL} slug={slug!r} level mismatch: "
                  f"DB={existing.level} YAML={expected_level}")
            raise StructuralMismatchError(
                f"slug={slug!r}: level mismatch"
            )
        if existing.parent_id != expected_parent_id:
            print(f"{PREFIX_FAIL} slug={slug!r} parent_id mismatch: "
                  f"DB={existing.parent_id} YAML={expected_parent_id}")
            raise StructuralMismatchError(
                f"slug={slug!r}: parent_id mismatch"
            )

        # Soft drift checks
        drifts = []
        if existing.name != name:
            drifts.append(f"name: DB={existing.name!r} YAML={name!r}")
        if existing.description != description:
            drifts.append("description differs")
        if drifts:
            print(f"{PREFIX_WARN} slug={slug!r} drift: " + "; ".join(drifts))
            stats["warned"] += 1

        if expected_level == 0:
            stats["parents_skipped"] += 1
        else:
            stats["leaves_skipped"] += 1
        print(f"{PREFIX_SKIP} slug={slug!r} (existing, level={expected_level})")
        return existing.id

    # New concept — insert and flush so leaves can resolve our id.
    concept = Concept(
        slug=slug,
        name=name,
        description=description,
        level=expected_level,
        parent_id=expected_parent_id,
    )
    session.add(concept)
    session.flush()  # populates concept.id via INSERT ... RETURNING

    if expected_level == 0:
        stats["parents_added"] += 1
    else:
        stats["leaves_added"] += 1
    print(f"{PREFIX_ADD} slug={slug!r} (new, level={expected_level})")
    return concept.id


# -----------------------------------------------------------------------------
# Main loader
# -----------------------------------------------------------------------------
def load_concepts(yaml_path: Path, dry_run: bool) -> int:
    """Drive the load. Returns process exit code (0/1/2)."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set. Example:")
        print('  $env:DATABASE_URL = '
              '"postgresql://atlas:atlas@localhost:5432/atlas_dev"')
        return 2

    if not yaml_path.exists():
        print(f"ERROR: YAML not found at {yaml_path}")
        return 2

    mode_label = "DRY-RUN" if dry_run else "LIVE"
    print(f"--- load_concepts.py ({mode_label}) ---")
    print(f"YAML:   {yaml_path}")
    print(f"DB:     {db_url.rsplit('@', 1)[-1]}")
    print()

    try:
        categories = load_yaml(yaml_path)
    except (yaml.YAMLError, ValueError) as e:
        print(f"ERROR: failed to parse YAML: {e}")
        return 2

    engine = create_engine(db_url)
    stats = {
        "parents_added": 0,
        "parents_skipped": 0,
        "leaves_added": 0,
        "leaves_skipped": 0,
        "warned": 0,
    }

    aborted = False
    with Session(engine) as session:
        try:
            for cat in categories:
                parent_id = process_concept(
                    session,
                    slug=cat["id"],
                    name=cat["name"],
                    description=normalize_description(cat.get("description")),
                    expected_level=0,
                    expected_parent_id=None,
                    stats=stats,
                )
                for leaf in cat.get("leaf_concepts", []):
                    process_concept(
                        session,
                        slug=leaf["id"],
                        name=leaf["name"],
                        description=normalize_description(leaf.get("description")),
                        expected_level=1,
                        expected_parent_id=parent_id,
                        stats=stats,
                    )

            if dry_run:
                session.rollback()
            else:
                session.commit()
        except StructuralMismatchError as e:
            session.rollback()
            aborted = True
            print(f"\nABORTED: {e}")
        except Exception:
            session.rollback()
            raise

    print()
    print("--- Summary ---")
    print(f"  Parents added:    {stats['parents_added']}")
    print(f"  Parents skipped:  {stats['parents_skipped']}")
    print(f"  Leaves added:     {stats['leaves_added']}")
    print(f"  Leaves skipped:   {stats['leaves_skipped']}")
    print(f"  Drift warnings:   {stats['warned']}")

    if aborted:
        return 1

    if dry_run:
        print()
        print("DRY-RUN: no changes committed.")

    return 0


# -----------------------------------------------------------------------------
# CLI entry point
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load taxonomy YAML into the concepts table.",
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        default=DEFAULT_YAML,
        help=f"Path to taxonomy YAML (default: {DEFAULT_YAML})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    args = parser.parse_args()
    return load_concepts(yaml_path=args.yaml, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
