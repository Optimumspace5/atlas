"""TF-IDF text-similarity ranker.

Ranks candidate books by cosine similarity between the user's reading
history and each candidate, in a TF-IDF vector space fit over every
book's title + author + description text.

Mechanic:
    1. Build per-book document strings: f"{title} {author} {description}".
       Books with no description fall back to title + author only,
       producing sparse vectors that struggle to match anything — that's
       an honest signal about description coverage in the corpus, not a bug.
    2. Fit a sklearn TfidfVectorizer over the full corpus (refit per call,
       v1 simplicity; 468 books fits in <100ms — optimize via a cached
       vectorizer if profiling shows it matters).
    3. User vector = sum of read books' TF-IDF vectors. Cosine similarity
       is scale-invariant, so sum vs mean produces identical rankings.
    4. Score each candidate by cosine_similarity(user_vector, candidate_vector).
    5. Filter already-read books, sort descending, return top_k.

Symmetry with rank_by_popularity (popularity.py): the signature
(session, read_book_ids, top_k) -> [(Book, float)] matches so the
router can dispatch uniformly across all three strategies.

Edge case: empty read_book_ids returns []. There is no query vector to
score against. The router (or eval harness) decides whether to fall back
to a default strategy in that case.
"""
import uuid

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Book


# TF-IDF vectorizer parameters. Conservative defaults; tune if the vocab
# turns out to be too large/small or rare-term noise dominates.
TFIDF_PARAMS = {
    "ngram_range": (1, 2),    # unigrams + bigrams capture phrases like "free cash flow"
    "stop_words": "english",  # drop the/and/of/etc — pure noise for similarity
    "min_df": 2,              # term must appear in 2+ books — filters typos and one-offs
    "max_df": 0.8,            # drop terms in >80% of books — too common to discriminate
    "sublinear_tf": True,     # log-scale TF — dampens "this title repeats X 8 times"
}

def _build_tfidf_matrix(session: Session):
    """Load all books and fit a TF-IDF matrix over their text content.

    Returns (books, matrix) where:
        books: list[Book] in stable row order.
        matrix: scipy.sparse.csr_matrix of shape (len(books), vocab_size).
                Row i corresponds to books[i].

    Books with no description fall back to "title author" alone. Their
    rows will be sparse and will struggle to match anything via cosine
    similarity — an honest signal about description coverage, not a bug.
    """
    books = list(session.execute(select(Book)).scalars().all())
    docs = [
        f"{b.title} {b.author} {b.description or ''}".strip()
        for b in books
    ]
    vectorizer = TfidfVectorizer(**TFIDF_PARAMS)
    matrix = vectorizer.fit_transform(docs)
    return books, matrix

def rank_by_tfidf(
    session: Session,
    read_book_ids: list[uuid.UUID],
    top_k: int,
) -> list[tuple[Book, float]]:
    """Rank books by TF-IDF cosine similarity to the user's reading history.

    Returns the top_k most-similar books with their cosine similarity
    scores, excluding already-read books. Returns [] if the user has
    read no books (no query vector to score against) or if none of the
    read book IDs match the catalog.

    Cosine similarities are in [0, 1] since TF-IDF vectors are non-negative.
    """
    if not read_book_ids:
        return []

    books, matrix = _build_tfidf_matrix(session)
    read_set = set(read_book_ids)
    read_indices = [i for i, b in enumerate(books) if b.id in read_set]
    if not read_indices:
        return []

    # User vector = sum of read books' TF-IDF vectors. Cosine similarity
    # is scale-invariant so sum vs mean produces identical rankings.
    user_vector = np.asarray(matrix[read_indices].sum(axis=0))
    sims = cosine_similarity(user_vector, matrix).ravel()

    scored = [
        (book, float(sims[i]))
        for i, book in enumerate(books)
        if book.id not in read_set
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]
