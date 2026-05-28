"use client";

import { useEffect, useState } from "react";
import {
  explainRecommendation,
  getRecommendations,
  type BookResult,
  type Strategy,
} from "@/lib/api";


const TEST_USER_ID = "00000000-0000-0000-0000-000000000001";

const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "gap", label: "Gap-fill" },
  { value: "popularity", label: "Popularity" },
  { value: "tfidf", label: "TF-IDF" },
];


type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; books: BookResult[] }
  | { kind: "error"; message: string };

type ExplainState =
  | { kind: "idle" }
  | { kind: "loading" }
  | {
      kind: "loaded";
      explanation: string;
      cached: boolean;
      quotaRemaining: number;
    }
  | { kind: "error"; message: string };


export default function RecommendationsPage() {
  const [strategy, setStrategy] = useState<Strategy>("gap");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [explanations, setExplanations] = useState<
    Record<string, ExplainState>
  >({});

  // Re-fetch whenever strategy changes.
  useEffect(() => {
    setState({ kind: "loading" });
    getRecommendations(TEST_USER_ID, strategy)
      .then((books) => setState({ kind: "loaded", books }))
      .catch((e) =>
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : "Unknown error",
        }),
      );
  }, [strategy]);

  async function handleExplain(bookId: string) {
    setExplanations((prev) => ({ ...prev, [bookId]: { kind: "loading" } }));
    try {
      const res = await explainRecommendation(TEST_USER_ID, bookId);
      setExplanations((prev) => ({
        ...prev,
        [bookId]: {
          kind: "loaded",
          explanation: res.explanation,
          cached: res.cached,
          quotaRemaining: res.quota_remaining,
        },
      }));
    } catch (e) {
      setExplanations((prev) => ({
        ...prev,
        [bookId]: {
          kind: "error",
          message: e instanceof Error ? e.message : "Unknown error",
        },
      }));
    }
  }

  return (
    <main className="w-full max-w-4xl flex flex-col">
      <header className="mt-8 mb-6">
        <h1 className="text-3xl font-bold text-gray-900">Recommendations</h1>
        <p className="text-gray-600 mt-1">
          Ranked by how much each book fills your current gaps.
        </p>
      </header>

      {/* Strategy switcher */}
      <div className="flex gap-2 mb-6">
        {STRATEGIES.map((s) => (
          <button
            key={s.value}
            type="button"
            onClick={() => setStrategy(s.value)}
            className={
              "px-3 py-1.5 text-sm font-medium rounded-md border transition " +
              (strategy === s.value
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50")
            }
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* Body */}
      {state.kind === "loading" && (
        <p className="text-gray-500">Loading recommendations…</p>
      )}
      {state.kind === "error" && (
        <p className="text-red-700">Failed to load: {state.message}</p>
      )}
      {state.kind === "loaded" && state.books.length === 0 && (
        <p className="text-gray-500">
          No recommendations yet — add some books to your library first.
        </p>
      )}
      {state.kind === "loaded" && state.books.length > 0 && (
        <ul className="space-y-3">
          {state.books.map((book, i) => {
            const expState: ExplainState =
              explanations[book.id] ?? { kind: "idle" };
            return (
              <li
                key={book.id}
                className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <div className="text-xs text-gray-500 font-mono">
                      #{i + 1}
                    </div>
                    <h2 className="font-semibold text-gray-900 leading-snug mt-1">
                      {book.title}
                    </h2>
                    <p className="text-sm text-gray-600">{book.author}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      Score:{" "}
                      <span className="font-mono">{book.score.toFixed(2)}</span>
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleExplain(book.id)}
                    disabled={expState.kind === "loading"}
                    className="shrink-0 px-3 py-1.5 text-sm font-medium bg-gray-900 text-white rounded-md hover:bg-gray-700 disabled:opacity-50"
                  >
                    {expState.kind === "loading" ? "Thinking…" : "Why this?"}
                  </button>
                </div>

                {expState.kind === "loaded" && (
                  <div className="mt-3 p-3 bg-blue-50 border border-blue-100 rounded text-sm text-gray-800">
                    <p className="leading-relaxed">{expState.explanation}</p>
                    <p className="text-xs text-gray-500 mt-2">
                      {expState.cached ? "Cached" : "Newly generated"} ·{" "}
                      {expState.quotaRemaining} explanations left today
                    </p>
                  </div>
                )}
                {expState.kind === "error" && (
                  <p className="mt-3 text-sm text-red-700">
                    {expState.message}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
