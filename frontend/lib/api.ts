/**
 * Tiny API client for the Atlas FastAPI backend.
 *
 * NEXT_PUBLIC_API_URL is read at build time (and at request time on the
 * client). Default falls back to the dev-mode localhost backend.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";


// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export type HealthResponse = {
  status: string;
};

export type BookSearchResult = {
  id: string;
  title: string;
  author: string;
  isbn_13: string | null;
  publication_year: number | null;
  cover_url: string | null;
};


// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------
export async function getHealth(): Promise<HealthResponse> {
  const r = await fetch(`${API_URL}/health`);
  if (!r.ok) {
    throw new Error(`Health check failed: ${r.status}`);
  }
  return r.json();
}


export async function searchBooks(
  query: string,
  limit = 10,
): Promise<BookSearchResult[]> {
  const url = new URL(`${API_URL}/books`);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", String(limit));
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`Search failed: ${r.status}`);
  }
  return r.json();
}


export async function addUserBook(
  userId: string,
  bookId: string,
): Promise<void> {
  const r = await fetch(`${API_URL}/users/${userId}/books`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ book_id: bookId }),
  });
  if (!r.ok) {
    throw new Error(`Add book failed: ${r.status}`);
  }
}

export async function getUserBooks(userId: string): Promise<BookSearchResult[]> {
  const r = await fetch(`${API_URL}/users/${userId}/books`);
  if (!r.ok) {
    throw new Error(`Failed to load library: ${r.status}`);
  }
  return r.json();
}


// ---------------------------------------------------------------------------
// Recommendation endpoints
// ---------------------------------------------------------------------------
export type Strategy = "gap" | "popularity" | "tfidf";

export type BookResult = {
  id: string;
  title: string;
  author: string;
  cover_url: string | null;
  score: number;
};

export type ExplainResponse = {
  book_id: string;
  explanation: string;
  model: string;
  cached: boolean;
  quota_remaining: number;
};


export async function getRecommendations(
  userId: string,
  strategy: Strategy = "gap",
  topK = 10,
): Promise<BookResult[]> {
  const url = new URL(`${API_URL}/recommendations/${userId}`);
  url.searchParams.set("strategy", strategy);
  url.searchParams.set("top_k", String(topK));
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`Recommendations failed: ${r.status}`);
  }
  const body = await r.json();
  return body.recommendations;
}


export async function explainRecommendation(
  userId: string,
  bookId: string,
): Promise<ExplainResponse> {
  const r = await fetch(`${API_URL}/recommendations/${userId}/explain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ book_id: bookId }),
  });
  if (!r.ok) {
    // FastAPI puts user-facing message in body.detail
    let detail = "";
    try {
      const body = await r.json();
      detail = body.detail ?? "";
    } catch {
      // body wasn't JSON; fall back to status text
    }
    throw new Error(detail || `Explain failed: ${r.status}`);
  }
  return r.json();
}


// ---------------------------------------------------------------------------
// Concept hierarchy
// ---------------------------------------------------------------------------
export type ConceptLeaf = {
  slug: string;
  name: string;
};

export type ConceptParent = {
  slug: string;
  name: string;
  leaves: ConceptLeaf[];
};


export async function getConcepts(): Promise<ConceptParent[]> {
  const r = await fetch(`${API_URL}/concepts`);
  if (!r.ok) {
    throw new Error(`Failed to load concepts: ${r.status}`);
  }
  return r.json();
}


// ---------------------------------------------------------------------------
// Coverage
// ---------------------------------------------------------------------------
export type CoverageResponse = {
  user_id: string;
  read_book_count: number;
  covered_count: number;
  coverage: Record<string, number>;
};


export async function getUserCoverage(userId: string): Promise<CoverageResponse> {
  const r = await fetch(`${API_URL}/users/${userId}/coverage`);
  if (!r.ok) {
    throw new Error(`Failed to load coverage: ${r.status}`);
  }
  return r.json();
}
