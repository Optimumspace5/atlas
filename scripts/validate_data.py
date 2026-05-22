"""Validate the v1 dataset against the spec before tagging v0.2.0.

Runs 11 integrity checks across the books, concepts, and book_concept_annotations
tables. Exits 0 if all checks pass; exits 1 if any check fails.

Checks:
    1. books row count == 468
    2. concepts row count == 56
    3. book_concept_annotations row count == 519
    4. exactly 8 level=0 concepts and 48 level=1 concepts
    5. every level=1 concept has a level=0 parent via parent_id
    6. every level=0 concept has parent_id IS NULL
    7. all annotation_type values in {manual, manual_audit, model}
    8. all strength values in {1.0, 0.5, 0.3}
    9. no annotations reference a level=0 concept (leaves-only rule)
   10. every leaf concept has >= 3 distinct books annotated against it
   11. every leaf concept has >= 1 confirmed (strength=1.0) annotation

Usage:
    python scripts/validate_data.py

Requires DATABASE_URL in env, e.g.:
    $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"
"""
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, Concept, BookConceptAnnotation  # noqa: E402


# -----------------------------------------------------------------------------
# Expected v1 dataset constants
# -----------------------------------------------------------------------------
EXPECTED_BOOKS = 468
EXPECTED_CONCEPTS = 56
EXPECTED_ANNOTATIONS = 519
EXPECTED_PARENTS = 8
EXPECTED_LEAVES = 48

VALID_ANNOTATION_TYPES = {"manual", "manual_audit", "model"}
VALID_STRENGTHS = {1.0, 0.5, 0.3}

MIN_BOOKS_PER_LEAF = 3
MIN_CONFIRMED_PER_LEAF = 1

PREFIX_PASS = "[PASS]"
PREFIX_FAIL = "[FAIL]"


# -----------------------------------------------------------------------------
# Check functions
# Each returns (passed: bool, detail: str). Detail is a short human-readable
# string printed alongside PASS/FAIL.
# -----------------------------------------------------------------------------
def check_books_count(session: Session) -> tuple[bool, str]:
    actual = session.scalar(select(func.count(Book.id)))
    ok = actual == EXPECTED_BOOKS
    return ok, f"expected {EXPECTED_BOOKS}, got {actual}"


def check_concepts_count(session: Session) -> tuple[bool, str]:
    actual = session.scalar(select(func.count(Concept.id)))
    ok = actual == EXPECTED_CONCEPTS
    return ok, f"expected {EXPECTED_CONCEPTS}, got {actual}"


def check_annotations_count(session: Session) -> tuple[bool, str]:
    actual = session.scalar(select(func.count()).select_from(BookConceptAnnotation))
    ok = actual == EXPECTED_ANNOTATIONS
    return ok, f"expected {EXPECTED_ANNOTATIONS}, got {actual}"


def check_concept_level_split(session: Session) -> tuple[bool, str]:
    rows = session.execute(
        select(Concept.level, func.count()).group_by(Concept.level)
    ).all()
    by_level = {level: count for level, count in rows}
    parents = by_level.get(0, 0)
    leaves = by_level.get(1, 0)
    ok = parents == EXPECTED_PARENTS and leaves == EXPECTED_LEAVES
    return ok, f"level=0: {parents} (expect {EXPECTED_PARENTS}), level=1: {leaves} (expect {EXPECTED_LEAVES})"


def check_leaves_have_parent(session: Session) -> tuple[bool, str]:
    """Every level=1 concept must have parent_id pointing to a level=0 concept."""
    orphans = session.execute(text("""
        SELECT c.slug
        FROM concepts c
        WHERE c.level = 1
          AND (
              c.parent_id IS NULL
              OR c.parent_id NOT IN (SELECT id FROM concepts WHERE level = 0)
          )
    """)).scalars().all()
    if not orphans:
        return True, "0 orphan leaves"
    return False, f"{len(orphans)} orphan leaves: {orphans[:5]}"


def check_parents_have_no_parent(session: Session) -> tuple[bool, str]:
    bad = session.execute(
        select(Concept.slug).where(Concept.level == 0, Concept.parent_id.is_not(None))
    ).scalars().all()
    if not bad:
        return True, "0 parents with parent_id set"
    return False, f"{len(bad)} parents with non-NULL parent_id: {bad[:5]}"


def check_annotation_types(session: Session) -> tuple[bool, str]:
    distinct = session.execute(
        select(BookConceptAnnotation.annotation_type).distinct()
    ).scalars().all()
    bad = [t for t in distinct if t not in VALID_ANNOTATION_TYPES]
    if not bad:
        return True, f"all values in {sorted(VALID_ANNOTATION_TYPES)}"
    return False, f"unexpected types: {bad}"


def check_strength_values(session: Session) -> tuple[bool, str]:
    distinct = session.execute(
        select(BookConceptAnnotation.strength).distinct()
    ).scalars().all()
    bad = [s for s in distinct if s not in VALID_STRENGTHS]
    if not bad:
        return True, f"all values in {sorted(VALID_STRENGTHS)}"
    return False, f"unexpected strengths: {bad}"


def check_no_annotations_on_parents(session: Session) -> tuple[bool, str]:
    """The leaves-only rule: annotations may only point to level=1 concepts."""
    count = session.scalar(text("""
        SELECT COUNT(*)
        FROM book_concept_annotations bca
        JOIN concepts c ON c.id = bca.concept_id
        WHERE c.level = 0
    """))
    if count == 0:
        return True, "0 annotations target parent concepts"
    return False, f"{count} annotations target parent concepts"


def check_leaf_book_coverage(session: Session) -> tuple[bool, str]:
    """Every leaf must have >= MIN_BOOKS_PER_LEAF distinct books annotated."""
    under = session.execute(text("""
        SELECT c.slug, COUNT(DISTINCT bca.book_id) AS n
        FROM concepts c
        LEFT JOIN book_concept_annotations bca ON bca.concept_id = c.id
        WHERE c.level = 1
        GROUP BY c.slug
        HAVING COUNT(DISTINCT bca.book_id) < :threshold
        ORDER BY n
    """), {"threshold": MIN_BOOKS_PER_LEAF}).all()
    if not under:
        return True, f"all leaves have >= {MIN_BOOKS_PER_LEAF} books"
    detail = ", ".join(f"{slug}={n}" for slug, n in under[:5])
    return False, f"{len(under)} under-covered leaves: {detail}"


def check_leaf_confirmed_coverage(session: Session) -> tuple[bool, str]:
    """Every leaf must have >= MIN_CONFIRMED_PER_LEAF confirmed annotations."""
    under = session.execute(text("""
        SELECT c.slug, COUNT(*) FILTER (WHERE bca.strength = 1.0) AS n
        FROM concepts c
        LEFT JOIN book_concept_annotations bca ON bca.concept_id = c.id
        WHERE c.level = 1
        GROUP BY c.slug
        HAVING COUNT(*) FILTER (WHERE bca.strength = 1.0) < :threshold
        ORDER BY n
    """), {"threshold": MIN_CONFIRMED_PER_LEAF}).all()
    if not under:
        return True, f"all leaves have >= {MIN_CONFIRMED_PER_LEAF} confirmed"
    detail = ", ".join(f"{slug}={n}" for slug, n in under[:5])
    return False, f"{len(under)} leaves below confirmed floor: {detail}"


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------
CHECKS = [
    ("books row count", check_books_count),
    ("concepts row count", check_concepts_count),
    ("annotations row count", check_annotations_count),
    ("concept level split (8/48)", check_concept_level_split),
    ("leaves have valid parent", check_leaves_have_parent),
    ("parents have no parent_id", check_parents_have_no_parent),
    ("annotation_type whitelist", check_annotation_types),
    ("strength whitelist", check_strength_values),
    ("no annotations on parents (leaves-only)", check_no_annotations_on_parents),
    (f"leaf book coverage >= {MIN_BOOKS_PER_LEAF}", check_leaf_book_coverage),
    (f"leaf confirmed coverage >= {MIN_CONFIRMED_PER_LEAF}", check_leaf_confirmed_coverage),
]


def validate() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set. Example:")
        print('  $env:DATABASE_URL = '
              '"postgresql://atlas:atlas@localhost:5432/atlas_dev"')
        return 2

    print("--- validate_data.py ---")
    print(f"DB:     {db_url.rsplit('@', 1)[-1]}")
    print()

    engine = create_engine(db_url)
    passed = 0
    failed = 0
    with Session(engine) as session:
        for name, fn in CHECKS:
            ok, detail = fn(session)
            prefix = PREFIX_PASS if ok else PREFIX_FAIL
            print(f"{prefix} {name}: {detail}")
            if ok:
                passed += 1
            else:
                failed += 1

    print()
    print("--- Summary ---")
    print(f"  Total checks: {passed + failed}")
    print(f"  Passed:       {passed}")
    print(f"  Failed:       {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(validate())
