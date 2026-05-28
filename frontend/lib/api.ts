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
