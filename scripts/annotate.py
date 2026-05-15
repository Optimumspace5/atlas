"""Interactive manual annotation CLI for the Atlas v1 book corpus.

The CLI reads the Week 1 merged corpus, loads the v0.1 taxonomy leaves, and
helps an annotator add leaf-level concept annotations with validated strength
scores. Annotations are stored in `data/annotations_v1.csv` using atomic writes.
"""

# ---------------------------------------------------------------------------
# 1. Module setup
# ---------------------------------------------------------------------------
import csv
import html
import logging
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CORPUS_CSV = Path("data/corpus_merged_v1.csv")
TAXONOMY_YAML = Path("data/taxonomy_v0.1.yaml")
ANNOTATIONS_CSV = Path("data/annotations_v1.csv")

ALLOWED_STRENGTHS: dict[str, str] = {
    "1.0": "confirmed",
    "0.5": "weak",
    "0.3": "conditional",
}

STRENGTH_ALIASES: dict[str, str] = {
    "1": "1.0",
    "1.0": "1.0",
    "confirmed": "1.0",
    "c": "1.0",
    "0.5": "0.5",
    ".5": "0.5",
    "weak": "0.5",
    "w": "0.5",
    "0.3": "0.3",
    ".3": "0.3",
    "conditional": "0.3",
    "cond": "0.3",
}

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

ISBN_COLUMNS = ["canonical_isbn_13", "isbn_13", "isbn_10"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 2. Data loading
# ---------------------------------------------------------------------------
def _clean_cell(value: Any) -> str:
    """Return a stripped string for CSV values."""
    return "" if value is None else str(value).strip()


def _normalize_isbn(value: Any) -> str:
    """Normalize ISBN text for lookup comparisons."""
    return re.sub(r"[^0-9Xx]", "", _clean_cell(value)).upper()


def _book_key(book: dict[str, Any]) -> str:
    """Return a stable annotation key for a corpus row."""
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


def load_corpus(path: Path) -> list[dict[str, Any]]:
    """Load the merged corpus CSV and attach row numbers/book keys."""
    if not path.exists():
        raise FileNotFoundError(f"Corpus file not found: {path}")

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row_number, row in enumerate(reader, start=1):
            cleaned = {key: _clean_cell(value) for key, value in row.items()}
            cleaned["_row_number"] = row_number
            cleaned["_book_key"] = _book_key(cleaned)
            rows.append(cleaned)

    return rows


def _yaml_scalar(line: str) -> str:
    """Extract a simple YAML scalar value from a `key: value` line."""
    value = line.split(":", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_taxonomy(path: Path) -> dict[str, Any]:
    """Load parent categories and leaf concepts from taxonomy_v0.1.yaml."""
    if not path.exists():
        raise FileNotFoundError(f"Taxonomy file not found: {path}")

    parents: list[dict[str, str]] = []
    leaves: list[dict[str, str]] = []
    current_parent: Optional[dict[str, str]] = None
    current_leaf: Optional[dict[str, str]] = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("  - id: "):
            current_parent = {"slug": _yaml_scalar(raw_line), "name": ""}
            parents.append(current_parent)
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
            leaves.append(current_leaf)
        elif current_leaf is not None and raw_line.startswith("        name: "):
            current_leaf["name"] = _yaml_scalar(raw_line)

    leaves_by_slug = {leaf["slug"]: leaf for leaf in leaves}
    children_by_parent: dict[str, list[dict[str, str]]] = {
        parent["slug"]: [] for parent in parents
    }
    for leaf in leaves:
        children_by_parent[leaf["parent_slug"]].append(leaf)

    if len(parents) != 8 or len(leaves) != 48:
        raise RuntimeError(
            f"Unexpected taxonomy shape: {len(parents)} parents, {len(leaves)} leaves"
        )

    return {
        "parents": parents,
        "leaves": leaves,
        "leaves_by_slug": leaves_by_slug,
        "children_by_parent": children_by_parent,
    }


def load_annotations(path: Path) -> list[dict[str, Any]]:
    """Load existing annotations, returning an empty list when absent."""
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return [{key: _clean_cell(value) for key, value in row.items()} for row in reader]


def group_annotations_by_book(
    annotations: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group annotation rows by book key."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for annotation in annotations:
        key = _clean_cell(annotation.get("book_key"))
        if key:
            grouped.setdefault(key, []).append(annotation)
    return grouped


# ---------------------------------------------------------------------------
# 3. CLI primitives
# ---------------------------------------------------------------------------
def _prompt(prompt: str) -> str:
    """Read a stripped input value."""
    return input(prompt).strip()


def _clean_display_text(value: Any) -> str:
    """Remove basic HTML and normalize whitespace for terminal display."""
    text = html.unescape(_clean_cell(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _print_wrapped(label: str, value: Any, width: int = 100) -> None:
    """Print a label/value pair with wrapped text."""
    text = _clean_display_text(value)
    if not text:
        return

    prefix = f"{label}: "
    print(
        textwrap.fill(
            text,
            width=width,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
        )
    )


def _format_book_line(book: dict[str, Any]) -> str:
    """Return a compact one-line book label."""
    row = book.get("_row_number")
    title = _clean_cell(book.get("title")) or "(untitled)"
    author = _clean_cell(book.get("author")) or "(unknown author)"
    isbn = _clean_cell(book.get("canonical_isbn_13")) or _clean_cell(book.get("isbn_13"))
    suffix = f" [{isbn}]" if isbn else ""
    return f"{row}. {title} - {author}{suffix}"


def display_annotations(annotations: list[dict[str, Any]]) -> None:
    """Print existing annotations for a book."""
    if not annotations:
        print("Existing annotations: none")
        return

    print("Existing annotations:")
    for annotation in annotations:
        concept = annotation.get("concept_slug")
        strength = annotation.get("strength")
        label = annotation.get("strength_label")
        print(f"  - {concept} ({strength} {label})")
        notes = _clean_cell(annotation.get("notes"))
        if notes:
            _print_wrapped("    notes", notes, width=96)


def display_book(
    book: dict[str, Any],
    existing_annotations: list[dict[str, Any]],
) -> None:
    """Print book metadata and existing annotations."""
    print()
    print("=" * 100)
    print(_format_book_line(book))
    print("=" * 100)
    _print_wrapped("Subtitle", book.get("subtitle"))
    _print_wrapped("Author", book.get("author"))
    _print_wrapped("Year", book.get("publication_year"))
    _print_wrapped("ISBN-13", book.get("canonical_isbn_13") or book.get("isbn_13"))
    _print_wrapped("Source query", book.get("source_queries") or book.get("source_query"))
    _print_wrapped("Description", book.get("description"))
    print()
    display_annotations(existing_annotations)
    print()


def _confirm(prompt: str, default: bool = False) -> bool:
    """Prompt for yes/no confirmation."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = _prompt(prompt + suffix).lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


# ---------------------------------------------------------------------------
# 4. Book selection
# ---------------------------------------------------------------------------
def find_next_unannotated(
    corpus: list[dict[str, Any]],
    annotations_by_book: dict[str, list[dict[str, Any]]],
    start_index: int,
) -> tuple[Optional[dict[str, Any]], int]:
    """Return the next unannotated book at or after start_index."""
    for index in range(start_index, len(corpus)):
        book = corpus[index]
        if not annotations_by_book.get(book["_book_key"]):
            return book, index + 1
    return None, start_index


def find_book_by_row(
    corpus: list[dict[str, Any]],
    row_number: int,
) -> Optional[dict[str, Any]]:
    """Find a corpus row by 1-based row number."""
    if 1 <= row_number <= len(corpus):
        return corpus[row_number - 1]
    return None


def find_books_by_isbn(
    corpus: list[dict[str, Any]],
    isbn: str,
) -> list[dict[str, Any]]:
    """Find books matching any ISBN column."""
    needle = _normalize_isbn(isbn)
    if not needle:
        return []

    matches: list[dict[str, Any]] = []
    for book in corpus:
        for column in ISBN_COLUMNS:
            if _normalize_isbn(book.get(column)) == needle:
                matches.append(book)
                break
    return matches


def search_books(
    corpus: list[dict[str, Any]],
    term: str,
) -> list[dict[str, Any]]:
    """Search books by title, author, or ISBN text."""
    needle = term.lower().strip()
    if not needle:
        return []

    matches: list[dict[str, Any]] = []
    for book in corpus:
        haystack = " ".join(
            [
                _clean_cell(book.get("title")),
                _clean_cell(book.get("subtitle")),
                _clean_cell(book.get("author")),
                _clean_cell(book.get("canonical_isbn_13")),
                _clean_cell(book.get("isbn_13")),
                _clean_cell(book.get("isbn_10")),
            ]
        ).lower()
        if needle in haystack:
            matches.append(book)
    return matches


def _select_from_matches(matches: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Display search matches and allow row-number selection."""
    if not matches:
        print("No matches.")
        return None

    print()
    print(f"Matches: {len(matches)}")
    for book in matches[:20]:
        print("  " + _format_book_line(book))
    if len(matches) > 20:
        print(f"  ... {len(matches) - 20} more not shown")

    choice = _prompt("Select row number, or press Enter to continue: ")
    if not choice:
        return None
    if not choice.isdigit():
        print("Expected a row number.")
        return None

    selected = find_book_by_row(matches, int(choice))
    if selected is not None:
        return selected

    for book in matches:
        if int(book["_row_number"]) == int(choice):
            return book

    print("That row is not in the search results.")
    return None


def _resolve_selector(
    corpus: list[dict[str, Any]],
    selector: str,
) -> Optional[dict[str, Any]]:
    """Resolve a row number or ISBN selector to a book."""
    selector = selector.strip()
    if not selector:
        return None

    if selector.isdigit():
        return find_book_by_row(corpus, int(selector))

    matches = find_books_by_isbn(corpus, selector)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return _select_from_matches(matches)

    return None


def select_book(
    corpus: list[dict[str, Any]],
    annotations_by_book: dict[str, list[dict[str, Any]]],
    cursor: int,
) -> tuple[Optional[dict[str, Any]], int]:
    """Prompt for the next book to annotate or review."""
    while True:
        command = _prompt(
            "Book [Enter=next, row N, isbn X, search X, review N/ISBN, q]: "
        )
        lowered = command.lower()

        if lowered in {"q", "quit", "exit"}:
            return None, cursor

        if lowered in {"", "n", "next"}:
            book, next_cursor = find_next_unannotated(
                corpus,
                annotations_by_book,
                cursor,
            )
            if book is None:
                print("No unannotated books remain after the current cursor.")
                return None, cursor
            return book, next_cursor

        if lowered.startswith("row "):
            selector = command[4:].strip()
            book = _resolve_selector(corpus, selector)
            if book is None:
                print("No book found for that row.")
                continue
            return book, int(book["_row_number"])

        if lowered.startswith("isbn "):
            selector = command[5:].strip()
            matches = find_books_by_isbn(corpus, selector)
            book = matches[0] if len(matches) == 1 else _select_from_matches(matches)
            if book is None:
                continue
            return book, int(book["_row_number"])

        if lowered.startswith("search "):
            selected = _select_from_matches(search_books(corpus, command[7:]))
            if selected is not None:
                return selected, int(selected["_row_number"])
            continue

        if lowered.startswith("review "):
            book = _resolve_selector(corpus, command[7:].strip())
            if book is None:
                print("No book found to review.")
                continue
            display_book(book, annotations_by_book.get(book["_book_key"], []))
            continue

        if command.isdigit():
            book = find_book_by_row(corpus, int(command))
            if book is None:
                print("No book found for that row.")
                continue
            return book, int(book["_row_number"])

        print("Unknown command.")


# ---------------------------------------------------------------------------
# 5. Concept selection
# ---------------------------------------------------------------------------
def _print_parent_menu(taxonomy: dict[str, Any]) -> None:
    """Print parent categories."""
    print("Parent categories:")
    for index, parent in enumerate(taxonomy["parents"], start=1):
        print(f"  {index}. {parent['name']} ({parent['slug']})")


def _print_leaf_menu(leaves: list[dict[str, str]]) -> None:
    """Print leaf concepts."""
    for index, leaf in enumerate(leaves, start=1):
        print(f"  {index}. {leaf['name']} ({leaf['slug']})")


def _search_concepts(
    taxonomy: dict[str, Any],
    term: str,
) -> list[dict[str, str]]:
    """Search leaf concepts by slug or display name."""
    needle = term.lower().strip()
    if not needle:
        return []

    return [
        leaf
        for leaf in taxonomy["leaves"]
        if needle in leaf["slug"].lower() or needle in leaf["name"].lower()
    ]


def _select_leaf_from_matches(
    matches: list[dict[str, str]],
) -> Optional[dict[str, str]]:
    """Display concept matches and allow numeric selection."""
    if not matches:
        print("No concept matches.")
        return None

    for index, leaf in enumerate(matches[:20], start=1):
        print(f"  {index}. {leaf['name']} ({leaf['slug']})")
    if len(matches) > 20:
        print(f"  ... {len(matches) - 20} more not shown")

    choice = _prompt("Select concept number, or press Enter to continue: ")
    if not choice:
        return None
    if not choice.isdigit():
        print("Expected a number.")
        return None

    index = int(choice)
    if 1 <= index <= min(len(matches), 20):
        return matches[index - 1]

    print("Concept number out of range.")
    return None


def _choose_leaf_from_parent(
    taxonomy: dict[str, Any],
    parent: dict[str, str],
) -> Optional[dict[str, str]]:
    """Prompt for a leaf within a selected parent category."""
    leaves = taxonomy["children_by_parent"][parent["slug"]]

    while True:
        print()
        print(f"{parent['name']} ({parent['slug']})")
        _print_leaf_menu(leaves)
        choice = _prompt("Leaf [number, slug, b=back, done]: ").strip()
        lowered = choice.lower()

        if lowered in {"b", "back"}:
            return None
        if lowered in {"done", "d", "q"}:
            return None

        if choice in taxonomy["leaves_by_slug"]:
            return taxonomy["leaves_by_slug"][choice]

        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(leaves):
                return leaves[index - 1]

        print("Invalid leaf selection.")


def choose_concept(taxonomy: dict[str, Any]) -> Optional[dict[str, str]]:
    """Prompt for a leaf concept using parent menu or direct slug."""
    while True:
        print()
        _print_parent_menu(taxonomy)
        choice = _prompt("Concept [parent #, leaf slug, /search, done]: ").strip()
        lowered = choice.lower()

        if lowered in {"done", "d", "q", "quit"}:
            return None

        if choice in taxonomy["leaves_by_slug"]:
            return taxonomy["leaves_by_slug"][choice]

        if choice.startswith("/"):
            selected = _select_leaf_from_matches(
                _search_concepts(taxonomy, choice[1:])
            )
            if selected is not None:
                return selected
            continue

        if choice.isdigit():
            parent_index = int(choice)
            if 1 <= parent_index <= len(taxonomy["parents"]):
                selected = _choose_leaf_from_parent(
                    taxonomy,
                    taxonomy["parents"][parent_index - 1],
                )
                if selected is not None:
                    return selected
                continue

        print("Invalid concept selection.")


def prompt_strength() -> tuple[str, str]:
    """Prompt for and validate annotation strength."""
    while True:
        raw = _prompt("Strength [1.0 confirmed, 0.5 weak, 0.3 conditional]: ")
        key = STRENGTH_ALIASES.get(raw.lower())
        if key in ALLOWED_STRENGTHS:
            return key, ALLOWED_STRENGTHS[key]
        print("Invalid strength. Allowed values: 1.0, 0.5, 0.3.")


# ---------------------------------------------------------------------------
# 6. Annotation IO
# ---------------------------------------------------------------------------
def write_annotations(rows: list[dict[str, Any]], path: Path) -> None:
    """Write all annotation rows atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with tmp_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=ANNOTATION_COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            sanitized = {
                column: "" if row.get(column) is None else row.get(column)
                for column in ANNOTATION_COLUMNS
            }
            writer.writerow(sanitized)

    os.replace(tmp_path, path)


def annotation_exists(
    existing_annotations: list[dict[str, Any]],
    concept_slug: str,
) -> bool:
    """Return whether the book already has an annotation for a concept."""
    return any(
        annotation.get("concept_slug") == concept_slug
        for annotation in existing_annotations
    )


def build_annotation(
    book: dict[str, Any],
    concept: dict[str, str],
    strength: str,
    strength_label: str,
    notes: str,
) -> dict[str, Any]:
    """Build one annotation row."""
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "annotation_type": "manual",
        "book_key": book["_book_key"],
        "corpus_row": book["_row_number"],
        "google_volume_id": book.get("google_volume_id"),
        "canonical_isbn_13": book.get("canonical_isbn_13"),
        "isbn_13": book.get("isbn_13"),
        "isbn_10": book.get("isbn_10"),
        "title": book.get("title"),
        "author": book.get("author"),
        "concept_slug": concept["slug"],
        "concept_name": concept["name"],
        "parent_slug": concept["parent_slug"],
        "parent_name": concept["parent_name"],
        "strength": strength,
        "strength_label": strength_label,
        "notes": notes,
    }


def append_annotation_atomic(
    annotations: list[dict[str, Any]],
    annotation: dict[str, Any],
    path: Path,
) -> list[dict[str, Any]]:
    """Append an annotation by rewriting the CSV atomically."""
    updated = annotations + [annotation]
    write_annotations(updated, path)
    return updated


def prompt_existing_action() -> str:
    """Prompt for what to do with an already annotated book."""
    while True:
        choice = _prompt(
            "Already annotated. [a]dd more, [r]eview only, [s]kip, [q]uit "
            "(default r): "
        ).lower()
        if not choice:
            return "r"
        if choice in {"a", "add"}:
            return "a"
        if choice in {"r", "review"}:
            return "r"
        if choice in {"s", "skip"}:
            return "s"
        if choice in {"q", "quit"}:
            return "q"
        print("Invalid choice.")


# ---------------------------------------------------------------------------
# 7. Main loop
# ---------------------------------------------------------------------------
def main() -> int:
    """Run the annotation CLI."""
    log.info("Loading corpus from %s", CORPUS_CSV)
    corpus = load_corpus(CORPUS_CSV)

    log.info("Loading taxonomy from %s", TAXONOMY_YAML)
    taxonomy = load_taxonomy(TAXONOMY_YAML)

    log.info("Loading annotations from %s", ANNOTATIONS_CSV)
    annotations = load_annotations(ANNOTATIONS_CSV)
    annotations_by_book = group_annotations_by_book(annotations)

    log.info(
        "Ready: %d books, %d taxonomy leaves, %d existing annotations",
        len(corpus),
        len(taxonomy["leaves"]),
        len(annotations),
    )

    cursor = 0

    while True:
        book, cursor = select_book(corpus, annotations_by_book, cursor)
        if book is None:
            break

        book_key = book["_book_key"]
        existing = annotations_by_book.get(book_key, [])
        display_book(book, existing)

        if existing:
            action = prompt_existing_action()
            if action == "q":
                break
            if action in {"r", "s"}:
                continue

        while True:
            concept = choose_concept(taxonomy)
            if concept is None:
                break

            current_existing = annotations_by_book.get(book_key, [])
            if annotation_exists(current_existing, concept["slug"]):
                print(f"Already annotated for `{concept['slug']}`. Skipping duplicate.")
                continue

            strength, strength_label = prompt_strength()
            notes = _prompt("Notes/rationale (optional): ")

            annotation = build_annotation(
                book,
                concept,
                strength,
                strength_label,
                notes,
            )
            annotations = append_annotation_atomic(
                annotations,
                annotation,
                ANNOTATIONS_CSV,
            )
            annotations_by_book.setdefault(book_key, []).append(annotation)

            print(
                f"Saved: {concept['slug']} = {strength} "
                f"({strength_label}) for row {book['_row_number']}"
            )

            if not _confirm("Add another concept for this book?", default=True):
                break

    log.info("Done. Annotation file: %s", ANNOTATIONS_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
