/**
 * API client for the Atlas FastAPI backend.
 *
 * Per-user endpoints live under /me and require the Supabase access token
 * (attached automatically via authHeaders). Public endpoints — search,
 * concepts, health — need no auth.
 */
import { supabase } from "./supabase";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export type HealthResponse = { status: string };

export type BookSearchResult = {
  id: string;
  title: string;
  author: string;
  isbn_13: string | null;
  publication_year: number | null;
  cover_url: string | null;
};

export type Strategy = "hybrid" | "gap" | "popularity" | "tfidf" | "embedding";

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

export type ConceptLeaf = { slug: string; name: string };
export type ConceptParent = { slug: string; name: string; leaves: ConceptLeaf[] };

export type CoverageResponse = {
  user_id: string;
  read_book_count: number;
  covered_count: number;
  coverage: Record<string, number>;
};

// ---------------------------------------------------------------------------
// Public endpoints (no auth)
// ---------------------------------------------------------------------------
export async function getHealth(): Promise<HealthResponse> {
  const r = await fetch(`${API_URL}/health`);
  if (!r.ok) throw new Error(`Health check failed: ${r.status}`);
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
  if (!r.ok) throw new Error(`Search failed: ${r.status}`);
  return r.json();
}

export async function getConcepts(): Promise<ConceptParent[]> {
  const r = await fetch(`${API_URL}/concepts`);
  if (!r.ok) throw new Error(`Failed to load concepts: ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Authenticated /me endpoints
// ---------------------------------------------------------------------------
export async function getMyBooks(): Promise<BookSearchResult[]> {
  const r = await fetch(`${API_URL}/me/books`, { headers: await authHeaders() });
  if (!r.ok) throw new Error(`Failed to load library: ${r.status}`);
  return r.json();
}

export async function addMyBook(bookId: string): Promise<void> {
  const r = await fetch(`${API_URL}/me/books`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ book_id: bookId }),
  });
  if (!r.ok) throw new Error(`Add book failed: ${r.status}`);
}

export async function removeMyBook(bookId: string): Promise<void> {
  const r = await fetch(`${API_URL}/me/books/${bookId}`, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  if (!r.ok) throw new Error(`Remove book failed: ${r.status}`);
}

export async function getMyCoverage(): Promise<CoverageResponse> {
  const r = await fetch(`${API_URL}/me/coverage`, { headers: await authHeaders() });
  if (!r.ok) throw new Error(`Failed to load coverage: ${r.status}`);
  return r.json();
}

export async function getMyRecommendations(
  strategy: Strategy = "hybrid",
  topK = 10,
): Promise<BookResult[]> {
  const url = new URL(`${API_URL}/me/recommendations`);
  url.searchParams.set("strategy", strategy);
  url.searchParams.set("top_k", String(topK));
  const r = await fetch(url, { headers: await authHeaders() });
  if (!r.ok) throw new Error(`Recommendations failed: ${r.status}`);
  const body = await r.json();
  return body.recommendations;
}

export type RoadmapRole = "investor" | "trader";

export type RoadmapRung = {
  rung: number;
  book_id: string | null;
  title: string;
  author: string;
  cover_url: string | null;
  new_concepts: string[];
  why: string;
  read: boolean;
  is_next: boolean;
};

export type RoadmapResponse = {
  role: string;
  rungs: RoadmapRung[];
  read_count: number;
  total: number;
};

export async function getMyRoadmap(role: RoadmapRole): Promise<RoadmapResponse> {
  const r = await fetch(`${API_URL}/me/roadmaps/${role}`, {
    headers: await authHeaders(),
  });
  if (!r.ok) throw new Error(`Failed to load roadmap: ${r.status}`);
  return r.json();
}

export async function explainMyRecommendation(
  bookId: string,
): Promise<ExplainResponse> {
  const r = await fetch(`${API_URL}/me/recommendations/explain`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ book_id: bookId }),
  });
  if (!r.ok) {
    let detail = "";
    try {
      detail = (await r.json()).detail ?? "";
    } catch {
      // body wasn't JSON
    }
    throw new Error(detail || `Explain failed: ${r.status}`);
  }
  return r.json();
}
