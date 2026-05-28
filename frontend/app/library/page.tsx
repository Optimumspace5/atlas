"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getUserBooks, type BookSearchResult } from "@/lib/api";


// Hardcoded test user for now; real auth is post-v0.4.0.
const TEST_USER_ID = "00000000-0000-0000-0000-000000000001";


type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; books: BookSearchResult[] }
  | { kind: "error"; message: string };


export default function LibraryPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    getUserBooks(TEST_USER_ID)
      .then((books) => setState({ kind: "loaded", books }))
      .catch((e) =>
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : "Unknown error",
        }),
      );
  }, []);

  return (
    <main className="w-full max-w-4xl flex flex-col">
      <header className="mt-8 mb-6">
        <h1 className="text-3xl font-bold text-gray-900">Your Library</h1>
        <p className="text-gray-600 mt-1">Books you've logged as read.</p>
      </header>

      {state.kind === "loading" && (
        <p className="text-gray-500">Loading…</p>
      )}

      {state.kind === "error" && (
        <p className="text-red-700">Failed to load library: {state.message}</p>
      )}

      {state.kind === "loaded" && state.books.length === 0 && (
        <div className="border border-dashed border-gray-300 rounded-lg p-8 text-center">
          <p className="text-gray-600">Your library is empty.</p>
          <Link
            href="/"
            className="inline-block mt-3 text-blue-600 hover:underline text-sm font-medium"
          >
            Add your first book →
          </Link>
        </div>
      )}

      {state.kind === "loaded" && state.books.length > 0 && (
        <>
          <p className="text-sm text-gray-500 mb-4">
            {state.books.length} book{state.books.length === 1 ? "" : "s"} logged.
          </p>
          <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {state.books.map((book) => (
              <li
                key={book.id}
                className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm hover:shadow-md transition"
              >
                <h2 className="font-semibold text-gray-900 leading-snug">
                  {book.title}
                </h2>
                <p className="text-sm text-gray-600 mt-1">{book.author}</p>
                <p className="text-xs text-gray-500 mt-2">
                  {book.publication_year && <>{book.publication_year}</>}
                  {book.publication_year && book.isbn_13 && <> · </>}
                  {book.isbn_13 && <>ISBN {book.isbn_13}</>}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
