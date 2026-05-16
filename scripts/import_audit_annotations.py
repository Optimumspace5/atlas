"""Import manual audit annotations from `docs/audit_notes.md`.

The importer parses the YAML-style audit blocks, validates every leaf slug
against the frozen taxonomy, resolves each audit book to the merged corpus, and
appends only missing annotations to `data/annotations_v1.csv`. Existing
annotations are never overwritten; strength disagreements are written to
`scripts/audit_import_conflicts.log` for manual review.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

AUDIT_NOTES = Path("docs/audit_notes.md")
CORPUS_CSV = Path("data/corpus_merged_v1.csv")
ANNOTATIONS_CSV = Path("data/annotations_v1.csv")
TAXONOMY_YAML = Path("data/taxonomy_v0.1.yaml")
CONFLICT_LOG = Path("scripts/audit_import_conflicts.log")
SUMMARY_LOG = Path("scripts/audit_import_summary.log")

ANNOTATION_COLUMNS: list[str] = [
    "created_at",
    "annotation_type",
    "book_key",
    "corpus_row",
    "google_volume_id",
    "canonical_isbn_13",
    "isbn_13",
    "isbn_10",
    "title",
    "author",
    "concept_slug",
    "concept_name",
    "parent_slug",
    "parent_name",
    "strength",
    "strength_label",
    "notes",
]

STRENGTH_VALUES: dict[str, str] = {
    "confirmed": "1.0",
    "weak": "0.5",
    "conditional": "0.3",
}


def _clean_cell(value: Any) -> str:
    """Return a stripped string for CSV values."""
    return "" if value is None else str(value).strip()


def _normalize_isbn(value: Any) -> str:
    """Normalize ISBN text for lookup comparisons."""
    return re.sub(r"[^0-9Xx]", "", _clean_cell(value)).upper()


def _book_key(book: dict[str, Any]) -> str:
    """Return the stable annotation key used by annotate.py."""
    canonical = _normalize_isbn(book.get("canonical_isbn_13"))
    if canonical:
        return f"isbn13:{canonical}"
    isbn_13 = _normalize_isbn(book.get("isbn_13"))
    if isbn_13:
        return f"isbn13:{isbn_13}"
    google_id = _clean_cell(book.get("google_volume_id"))
    if google_id:
        return f"google:{google_id}"
    return f"row:{book.get('_row_number')}"


def _normalize_title(value: Any) -> str:
    """Normalize title-like text for fuzzy matching."""
    text = _clean_cell(value).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _title_similarity(candidate_title: str, target_title: str, subtitle: str = "") -> float:
    """Return a conservative similarity score for title matching."""
    target = _normalize_title(target_title)
    variants = [candidate_title]
    if ":" in candidate_title:
        variants.append(candidate_title.split(":", 1)[0])
    if subtitle:
        variants.append(f"{candidate_title} {subtitle}")
    return max(
        (
            SequenceMatcher(None, _normalize_title(variant), target).ratio()
            for variant in variants
            if _normalize_title(variant)
        ),
        default=0.0,
    )


def _last_name(author: str) -> str:
    """Return a simple last-name token."""
    tokens = re.findall(r"[A-Za-z0-9]+", author)
    return tokens[-1].lower() if tokens else ""


def _author_matches(candidate_author: Any, target_author: str) -> bool:
    """Return True when the target last name appears in candidate authors."""
    last = _last_name(target_author)
    if not last:
        return False
    tokens = re.findall(r"[A-Za-z0-9]+", _clean_cell(candidate_author).lower())
    return last in tokens


def parse_audit_headings(path: Path) -> list[dict[str, Any]]:
    """Extract audit heading metadata from audit notes."""
    pattern = re.compile(
        r"^###\s+Book\s+(\d+):\s+(.+?)\s+[\u2013\u2014-]\s+(.+?)\s*$"
    )
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            entries.append(
                {
                    "book_number": int(match.group(1)),
                    "title": match.group(2).strip(),
                    "author": match.group(3).strip(),
                }
            )
    if len(entries) != 17:
        raise RuntimeError(f"Expected 17 audit headings, found {len(entries)}")
    return entries


def _parse_strength_marker(comment: str) -> str:
    """Map an inline audit comment to a strength label."""
    lowered = comment.lower()
    if "conditional" in lowered:
        return "conditional"
    if "weak" in lowered:
        return "weak"
    return "confirmed"


def parse_audit_yaml_blocks(path: Path) -> dict[str, dict[str, Any]]:
    """Parse audit YAML code blocks keyed by audit block slug."""
    text = path.read_text(encoding="utf-8")
    heading_pattern = re.compile(
        r"^###\s+Book\s+(\d+):\s+(.+?)\s+[\u2013\u2014-]\s+(.+?)\s*$",
        re.MULTILINE,
    )
    headings = list(heading_pattern.finditer(text))
    result: dict[str, dict[str, Any]] = {}

    for idx, heading in enumerate(headings):
        start = heading.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        section = text[start:end]
        block_match = re.search(r"```yaml\s+(.*?)```", section, flags=re.DOTALL)
        if not block_match:
            raise RuntimeError(f"Missing YAML block for audit book {heading.group(1)}")

        block_lines = block_match.group(1).splitlines()
        book_slug: Optional[str] = None
        current_parent: Optional[str] = None
        tags: list[dict[str, str]] = []

        for raw_line in block_lines:
            if not raw_line.strip():
                continue
            if not raw_line.startswith(" ") and raw_line.rstrip().endswith(":"):
                book_slug = raw_line.strip().rstrip(":")
                continue
            parent_match = re.match(r"^\s{2}([a-z0-9_]+):\s*$", raw_line)
            if parent_match:
                current_parent = parent_match.group(1)
                continue
            leaf_match = re.match(r"^\s*-\s+([a-z0-9_]+)(?:\s*#\s*(.*))?$", raw_line)
            if leaf_match:
                if current_parent is None:
                    raise RuntimeError(f"Leaf without parent in audit book {heading.group(1)}")
                concept_slug = leaf_match.group(1)
                label = _parse_strength_marker(leaf_match.group(2) or "")
                tags.append(
                    {
                        "concept_slug": concept_slug,
                        "parent_slug": current_parent,
                        "strength_label": label,
                        "strength": STRENGTH_VALUES[label],
                    }
                )

        if not book_slug:
            raise RuntimeError(f"Missing audit block slug for book {heading.group(1)}")
        result[book_slug] = {
            "book_number": int(heading.group(1)),
            "title": heading.group(2).strip(),
            "author": heading.group(3).strip(),
            "tags": tags,
        }

    if len(result) != 17:
        raise RuntimeError(f"Expected 17 audit YAML blocks, found {len(result)}")
    return result


def load_corpus(path: Path) -> list[dict[str, Any]]:
    """Load corpus rows with row numbers and book keys."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=1):
            cleaned = {key: _clean_cell(value) for key, value in row.items()}
            cleaned["_row_number"] = str(row_number)
            cleaned["_book_key"] = _book_key(cleaned)
            rows.append(cleaned)
    return rows


def _yaml_scalar(line: str) -> str:
    """Extract a simple YAML scalar from a key/value line."""
    value = line.split(":", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_taxonomy(path: Path) -> dict[str, Any]:
    """Load taxonomy parent and leaf metadata from the frozen YAML file."""
    parents: dict[str, dict[str, str]] = {}
    leaves: dict[str, dict[str, str]] = {}
    current_parent: Optional[dict[str, str]] = None
    current_leaf: Optional[dict[str, str]] = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("  - id: "):
            current_parent = {"slug": _yaml_scalar(raw_line), "name": ""}
            parents[current_parent["slug"]] = current_parent
            current_leaf = None
        elif current_parent is not None and raw_line.startswith("    name: "):
            current_parent["name"] = _yaml_scalar(raw_line)
        elif current_parent is not None and raw_line.startswith("      - id: "):
            current_leaf = {
                "slug": _yaml_scalar(raw_line),
                "name": "",
                "parent_slug": current_parent["slug"],
                "parent_name": current_parent["name"],
            }
            leaves[current_leaf["slug"]] = current_leaf
        elif current_leaf is not None and raw_line.startswith("        name: "):
            current_leaf["name"] = _yaml_scalar(raw_line)

    if len(parents) != 8 or len(leaves) != 48:
        raise RuntimeError(f"Unexpected taxonomy shape: {len(parents)} parents, {len(leaves)} leaves")
    return {"parents": parents, "leaves": leaves}


def resolve_book_in_corpus(audit_book: dict[str, Any], corpus_rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Resolve an audit book to a corpus row by title and author."""
    matches: list[tuple[float, dict[str, Any]]] = []
    for row in corpus_rows:
        score = _title_similarity(row.get("title", ""), audit_book["title"], row.get("subtitle", ""))
        if score >= 0.80 and _author_matches(row.get("author", ""), audit_book["author"]):
            matches.append((score, row))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def load_existing_annotations(path: Path) -> list[dict[str, Any]]:
    """Load current annotations, returning an empty list when absent."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return [{key: _clean_cell(value) for key, value in row.items()} for row in reader]


def build_existing_index(
    annotations: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Index existing annotations by book key and concept slug."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for annotation in annotations:
        key = (_clean_cell(annotation.get("book_key")), _clean_cell(annotation.get("concept_slug")))
        if key[0] and key[1] and key not in index:
            index[key] = annotation
    return index


def _annotation_row(
    *,
    corpus_row: dict[str, Any],
    concept: dict[str, str],
    strength: str,
    strength_label: str,
    created_at: str,
) -> dict[str, str]:
    """Build one annotation CSV row from corpus and taxonomy data."""
    return {
        "created_at": created_at,
        "annotation_type": "manual_audit",
        "book_key": corpus_row["_book_key"],
        "corpus_row": corpus_row["_row_number"],
        "google_volume_id": corpus_row.get("google_volume_id", ""),
        "canonical_isbn_13": corpus_row.get("canonical_isbn_13", ""),
        "isbn_13": corpus_row.get("isbn_13", ""),
        "isbn_10": corpus_row.get("isbn_10", ""),
        "title": corpus_row.get("title", ""),
        "author": corpus_row.get("author", ""),
        "concept_slug": concept["slug"],
        "concept_name": concept["name"],
        "parent_slug": concept["parent_slug"],
        "parent_name": concept["parent_name"],
        "strength": strength,
        "strength_label": strength_label,
        "notes": "Imported from docs/audit_notes.md (v0.1, 2026-05-12)",
    }


def process_audit_entries(
    audit_blocks: dict[str, dict[str, Any]],
    corpus_rows: list[dict[str, Any]],
    taxonomy: dict[str, Any],
    existing_index: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """Return new annotation rows plus summary and conflict log lines."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_rows: list[dict[str, str]] = []
    summary: list[str] = []
    conflicts: list[str] = []

    for book_slug, audit_book in audit_blocks.items():
        corpus_row = resolve_book_in_corpus(audit_book, corpus_rows)
        if corpus_row is None:
            raise RuntimeError(f"Could not resolve audit book to corpus row: {audit_book['title']}")

        added = skipped = conflict_count = 0
        for tag in audit_book["tags"]:
            concept_slug = tag["concept_slug"]
            concept = taxonomy["leaves"][concept_slug]
            key = (corpus_row["_book_key"], concept_slug)
            existing = existing_index.get(key)
            if existing is None:
                row = _annotation_row(
                    corpus_row=corpus_row,
                    concept=concept,
                    strength=tag["strength"],
                    strength_label=tag["strength_label"],
                    created_at=created_at,
                )
                new_rows.append(row)
                existing_index[key] = row
                added += 1
                summary.append(
                    f"ADD: row={corpus_row['_row_number']} book={audit_book['title']} "
                    f"concept={concept_slug} strength={tag['strength']}"
                )
                continue

            if _clean_cell(existing.get("strength")) == tag["strength"]:
                skipped += 1
                summary.append(
                    f"SKIP existing: row={corpus_row['_row_number']} concept={concept_slug} "
                    f"strength={tag['strength']}"
                )
                continue

            conflict_count += 1
            conflicts.extend(
                [
                    (
                        f"CONFLICT: book={corpus_row.get('title')!r} corpus_row={corpus_row['_row_number']} "
                        f"book_key={corpus_row['_book_key']!r} concept={concept_slug!r}"
                    ),
                    (
                        f"  existing: strength={existing.get('strength')}, "
                        f"label={existing.get('strength_label')}, type={existing.get('annotation_type')}, "
                        f"created_at={existing.get('created_at')}"
                    ),
                    f"  audit:    strength={tag['strength']}, label={tag['strength_label']}",
                    "  decision: kept existing, audit version not imported",
                    "  action:   review manually if you want to overwrite",
                    "",
                ]
            )

        log.info(
            "%s: %d added, %d skipped, %d conflicts",
            book_slug,
            added,
            skipped,
            conflict_count,
        )

    return new_rows, summary, conflicts


def validate_audit_tags(
    audit_blocks: dict[str, dict[str, Any]],
    taxonomy: dict[str, Any],
) -> int:
    """Fail if any audit tag references a nonexistent leaf or wrong parent."""
    count = 0
    missing: list[str] = []
    parent_mismatches: list[str] = []
    for book_slug, audit_book in audit_blocks.items():
        for tag in audit_book["tags"]:
            count += 1
            concept_slug = tag["concept_slug"]
            concept = taxonomy["leaves"].get(concept_slug)
            if concept is None:
                missing.append(f"{book_slug}: {concept_slug}")
            elif concept["parent_slug"] != tag["parent_slug"]:
                parent_mismatches.append(
                    f"{book_slug}: {concept_slug} under {tag['parent_slug']} "
                    f"(taxonomy parent {concept['parent_slug']})"
                )

    if missing or parent_mismatches:
        lines = []
        if missing:
            lines.append("Missing leaf slugs:")
            lines.extend(f"  - {item}" for item in missing)
        if parent_mismatches:
            lines.append("Parent mismatches:")
            lines.extend(f"  - {item}" for item in parent_mismatches)
        raise RuntimeError("\n".join(lines))
    return count


def append_annotations(rows: list[dict[str, str]], path: Path) -> None:
    """Append annotations by rewriting the CSV atomically."""
    existing_rows: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            existing_rows = list(reader)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=ANNOTATION_COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in existing_rows:
            writer.writerow({column: "" if row.get(column) is None else row.get(column) for column in ANNOTATION_COLUMNS})
        for row in rows:
            writer.writerow({column: "" if row.get(column) is None else row.get(column) for column in ANNOTATION_COLUMNS})
    os.replace(tmp_path, path)


def write_log(path: Path, lines: list[str]) -> None:
    """Write a plain text log file."""
    if not lines:
        lines = ["No entries."]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    """Run the audit annotation import."""
    audit_blocks = parse_audit_yaml_blocks(AUDIT_NOTES)
    taxonomy = load_taxonomy(TAXONOMY_YAML)
    total_tags = validate_audit_tags(audit_blocks, taxonomy)
    corpus_rows = load_corpus(CORPUS_CSV)
    existing_annotations = load_existing_annotations(ANNOTATIONS_CSV)
    existing_index = build_existing_index(existing_annotations)

    unresolved = [
        audit_book["title"]
        for audit_book in audit_blocks.values()
        if resolve_book_in_corpus(audit_book, corpus_rows) is None
    ]
    if unresolved:
        raise RuntimeError("Could not resolve audit books:\n" + "\n".join(f"  - {title}" for title in unresolved))

    log.info("Parsed %d audit books with %d total tag assignments", len(audit_blocks), total_tags)
    log.info("Validated all %d slugs against %s", total_tags, TAXONOMY_YAML)
    log.info("Resolved %d of %d audit books to corpus rows", len(audit_blocks), len(audit_blocks))
    log.info("Processing audit entries...")

    new_rows, summary, conflicts = process_audit_entries(
        audit_blocks,
        corpus_rows,
        taxonomy,
        existing_index,
    )

    if new_rows:
        append_annotations(new_rows, ANNOTATIONS_CSV)
    write_log(SUMMARY_LOG, summary)
    write_log(CONFLICT_LOG, conflicts)

    log.info(
        "Done. %d added, %d summary lines, %d conflict blocks.",
        len(new_rows),
        len(summary),
        sum(1 for line in conflicts if line.startswith("CONFLICT:")),
    )
    log.info("Annotations now has %d rows.", len(existing_annotations) + len(new_rows))
    return 0 if not conflicts else 1


if __name__ == "__main__":
    sys.exit(main())
