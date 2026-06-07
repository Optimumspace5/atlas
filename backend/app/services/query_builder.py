"""Single source of truth for cross-encoder query and document strings.

Both training-pair generation (scripts/generate_training_data.py) AND
the production reranker MUST call into this module. Drift between how
queries are built at training time vs inference time silently degrades
reranker performance — the model learns to score one string format,
then sees a different one.

See docs/CROSS_ENCODER_DESIGN.md §3 (query) and §4 (candidate text)
for the locked rules. Changing the output of either function
invalidates training data already generated.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book, Concept
from backend.app.services.gap_scoring import get_gap_vector


TOP_GAPS_IN_QUERY = 5
RECENT_BOOKS_IN_QUERY = 2


def build_user_query(
    session: Session,
    read_book_ids: list[uuid.UUID],
    recent_book_ids: list[uuid.UUID] | None = None,
) -> str:
    """Format the hybrid (gaps + recent reading) query for one user.

    Output format (locked, §3):
        "Reader gaps: {name_1}; ...; {name_5}. Recent reading: {title} by {author}; {title} by {author}."

    Args:
        read_book_ids: full reading history. Used to compute the gap vector.
        recent_book_ids: explicit recent-reading list shown in the query
            text. If None, falls back to read_book_ids[:RECENT_BOOKS_IN_QUERY].
            Production callers MUST pass this (ordered by created_at DESC).
            Synthetic-training callers may omit it.

    Returns empty string for cold-start (no read books). Without this
    guard, get_gap_vector() would return many max-value gaps and produce
    a query of alphabetical-tie-break concepts — exactly the kind of
    silent bad query that wrecks training signal.

    Sections with no content are dropped entirely (no "Reader gaps: ."
    degeneracy).
    """
    if not read_book_ids:
        return ""

    parts: list[str] = []

    # ---- Gaps ----
    gap_vector = get_gap_vector(session, read_book_ids)
    top_gaps = sorted(
        ((slug, gap) for slug, gap in gap_vector.items() if gap > 0),
        key=lambda kv: (-kv[1], kv[0]),  # gap DESC, slug ASC tie-break (§3)
    )[:TOP_GAPS_IN_QUERY]

    if top_gaps:
        slugs = [slug for slug, _ in top_gaps]
        rows = session.execute(
            select(Concept.slug, Concept.name).where(Concept.slug.in_(slugs))
        ).all()
        name_by_slug = {slug: name for slug, name in rows}
        gap_names = [name_by_slug[slug] for slug, _ in top_gaps if slug in name_by_slug]
        if gap_names:
            parts.append(f"Reader gaps: {'; '.join(gap_names)}.")

    # ---- Recent reading ----
    recents = recent_book_ids if recent_book_ids is not None else read_book_ids
    recent_ids = list(recents[:RECENT_BOOKS_IN_QUERY])

    if recent_ids:
        rows = session.execute(
            select(Book.id, Book.title, Book.author).where(Book.id.in_(recent_ids))
        ).all()
        by_id = {bid: (title, author) for bid, title, author in rows}
        recent_strs = []
        for bid in recent_ids:  # preserve caller's order, not DB order
            row = by_id.get(bid)
            if row is None:
                continue
            title, author = row
            recent_strs.append(f"{title} by {author}")
        if recent_strs:
            parts.append(f"Recent reading: {'; '.join(recent_strs)}.")

    return " ".join(parts)


def build_candidate_text(book: Book) -> str:
    """Format a book as the candidate text shown to the reranker.

    Output format (locked, §4):
        "{title} {author} {description}"  -- or "{title} {author}" if no description.

    Whitespace-only descriptions are treated as missing, not concatenated
    blank. Title and author are also stripped to avoid trailing whitespace
    leaking into the model input.

    Matches the document composition used by services/tfidf.py and
    scripts/generate_embeddings.py — deliberate, to keep format
    consistent across all retrieval and ranking stages.
    """
    parts = [book.title.strip(), book.author.strip()]
    description = (book.description or "").strip()
    if description:
        parts.append(description)
    return " ".join(parts).strip()
