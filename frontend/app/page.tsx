"use client";

import { useEffect, useState } from "react";
import {
  addUserBook,
  getHealth,
  searchBooks,
  type BookSearchResult,
} from "@/lib/api";
import { BookCover } from "./components/BookCover";


// Hardcoded test user for now; real auth is post-v0.4.0.
const TEST_USER_ID = "00000000-0000-0000-0000-000000000001";

// Debounce delay in milliseconds.
const SEARCH_DEBOUNCE_MS = 250;

type HealthStatus = "checking" | "ok" | "error";
type AddState =
  | { kind: "idle" }
  | { kind: "adding"; bookId: string }
  | { kind: "added"; title: string }
  | { kind: "error"; message: string };


export default function HomePage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<BookSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [health, setHealth] = useState<HealthStatus>("checking");
  const [addState, setAddState] = useState<AddState>({ kind: "idle" });

  useEffect(() => {
    getHealth()
      .then((res) => setHealth(res.status === "ok" ? "ok" : "error"))
      .catch(() => setHealth("error"));
  }, []);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    const handle = setTimeout(() => {
      searchBooks(trimmed)
        .then((hits) => setResults(hits))
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(handle);
  }, [query]);

  async function handleAdd(book: BookSearchResult) {
    setAddState({ kind: "adding", bookId: book.id });
    try {
      await addUserBook(TEST_USER_ID, book.id);
      setAddState({ kind: "added", title: book.title });
      setQuery("");
      setResults([]);
    } catch (e) {
      setAddState({
        kind: "error",
        message: e instanceof Error ? e.message : "Unknown error",
      });
    }
  }

  return (
    <main className="w-full max-w-2xl flex flex-col items-center">
      <header className="w-full mt-12 mb-8">
        <h1 className="text-4xl font-bold text-gray-900">Atlas</h1>
        <p className="text-gray-600 mt-2">
          Knowledge-gap-aware book recommendations for investing and trading.
        </p>
      </header>

      <section className="w-full relative">
        <label
          htmlFor="search"
          className="block text-sm font-medium text-gray-700 mb-2"
        >
          Add a book to your library
        </label>
        <input
          id="search"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by title or author…"
          autoComplete="off"
          className="w-full border border-gray-300 rounded-lg p-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />

        {query.trim() && (
          <div className="mt-2 border border-gray-200 rounded-lg bg-white shadow-sm overflow-hidden">
            {searching && results.length === 0 && (
              <div className="p-3 text-sm text-gray-500">Searching…</div>
            )}
            {!searching && results.length === 0 && (
              <div className="p-3 text-sm text-gray-500">No matches in catalog.</div>
            )}
            {results.map((book) => (
              <button
                key={book.id}
                type="button"
                onClick={() => handleAdd(book)}
                disabled={addState.kind === "adding"}
                className="w-full text-left p-3 hover:bg-gray-50 border-b last:border-b-0 border-gray-100 disabled:opacity-50 flex gap-3 items-center"
              >
                <BookCover url={book.cover_url} title={book.title} size="sm" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-gray-900 truncate">{book.title}</div>
                  <div className="text-sm text-gray-600 truncate">
                    {book.author}
                    {book.publication_year && <> · {book.publication_year}</>}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}

        {addState.kind === "added" && (
          <p className="mt-3 text-sm text-green-700">
            Added: <span className="font-medium">{addState.title}</span>
          </p>
        )}
        {addState.kind === "error" && (
          <p className="mt-3 text-sm text-red-700">{addState.message}</p>
        )}
      </section>

      <footer className="w-full mt-auto pt-12 text-sm">
        <BackendStatus status={health} />
      </footer>
    </main>
  );
}


function BackendStatus({ status }: { status: HealthStatus }) {
  const label =
    status === "ok" ? "Backend OK"
    : status === "error" ? "Backend unreachable"
    : "Checking backend…";

  const dotClass =
    status === "ok" ? "bg-green-500"
    : status === "error" ? "bg-red-500"
    : "bg-gray-400 animate-pulse";

  return (
    <div className="flex items-center gap-2 text-gray-500">
      <span className={`inline-block w-2 h-2 rounded-full ${dotClass}`} />
      <span>{label}</span>
    </div>
  );
}
