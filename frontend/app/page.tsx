"use client";

import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import {
  addUserBook,
  getHealth,
  searchBooks,
  type BookSearchResult,
} from "@/lib/api";
import { BookCover } from "./components/BookCover";

const TEST_USER_ID = "00000000-0000-0000-0000-000000000001";
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
      return;
    }

    const handle = setTimeout(() => {
      searchBooks(trimmed)
        .then((hits) => setResults(hits))
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(handle);
  }, [query]);

  function handleQueryChange(value: string) {
    setQuery(value);

    if (!value.trim()) {
      setResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
  }

  async function handleAdd(book: BookSearchResult) {
    setAddState({ kind: "adding", bookId: book.id });

    try {
      await addUserBook(TEST_USER_ID, book.id);
      setAddState({ kind: "added", title: book.title });
      setQuery("");
      setResults([]);
      setSearching(false);
    } catch (e) {
      setAddState({
        kind: "error",
        message: e instanceof Error ? e.message : "Unknown error",
      });
    }
  }

  return (
    <main className="relative min-h-[calc(100vh-4rem)] overflow-hidden">
      <MarketMesh />

      <section className="relative z-10 ml-[6vw] flex min-h-[720px] max-w-4xl flex-col justify-center py-20">
        <p className="mb-8 text-sm font-semibold uppercase tracking-[0.38em] text-transparent bg-gradient-to-r from-cyan-300 via-blue-300 to-violet-300 bg-clip-text">
          Welcome Back, Clarence
        </p>

        <h1 className="max-w-4xl text-6xl font-semibold leading-[1.08] tracking-tight text-white sm:text-7xl lg:text-8xl">
          Master knowledge.
          <br />
          Close{" "}
          <span className="text-transparent bg-gradient-to-r from-cyan-300 via-blue-400 to-violet-400 bg-clip-text">
            every gap.
          </span>
        </h1>

        <p className="mt-10 max-w-2xl text-xl leading-9 text-slate-300">
          Atlas analyzes your reading across investing and trading to uncover
          what you&apos;re missing — and what to read next.
        </p>

        <section className="relative mt-10 max-w-2xl">
          <div className="relative">
            <Search className="pointer-events-none absolute left-6 top-1/2 h-6 w-6 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => handleQueryChange(e.target.value)}
              placeholder="Search by title or author..."
              autoComplete="off"
              className="h-20 w-full rounded-lg border border-white/12 bg-[#070b15]/70 pl-16 pr-6 text-lg text-white outline-none shadow-[0_0_40px_rgba(2,6,23,0.35)] backdrop-blur placeholder:text-slate-500 transition focus:border-violet-400/70 focus:ring-4 focus:ring-violet-500/10"
            />
          </div>

          {query.trim() && (
            <div className="absolute left-0 right-0 top-[calc(100%+0.75rem)] z-20 overflow-hidden rounded-lg border border-white/10 bg-[#080d18]/95 shadow-2xl backdrop-blur">
              {searching && results.length === 0 && (
                <div className="px-5 py-4 text-sm text-slate-400">Searching...</div>
              )}

              {!searching && results.length === 0 && (
                <div className="px-5 py-4 text-sm text-slate-400">
                  No matches in catalog.
                </div>
              )}

              {results.map((book) => (
                <button
                  key={book.id}
                  type="button"
                  onClick={() => handleAdd(book)}
                  disabled={addState.kind === "adding"}
                  className="flex w-full items-center gap-4 border-b border-white/8 px-5 py-4 text-left transition last:border-b-0 hover:bg-white/[0.04] disabled:opacity-50"
                >
                  <BookCover url={book.cover_url} title={book.title} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-white">{book.title}</div>
                    <div className="mt-1 truncate text-sm text-slate-400">
                      {book.author}
                      {book.publication_year && <> · {book.publication_year}</>}
                    </div>
                  </div>
                  <div className="rounded-md border border-violet-400/30 px-3 py-1.5 text-xs font-medium text-violet-300">
                    Add
                  </div>
                </button>
              ))}
            </div>
          )}

          {addState.kind === "added" && (
            <p className="mt-4 text-sm text-emerald-300">
              Added <span className="font-medium">{addState.title}</span> to your library.
            </p>
          )}

          {addState.kind === "error" && (
            <p className="mt-4 text-sm text-red-300">{addState.message}</p>
          )}

          <BackendStatus status={health} />
        </section>
      </section>
    </main>
  );
}

function BackendStatus({ status }: { status: HealthStatus }) {
  const label =
    status === "ok"
      ? "Backend OK"
      : status === "error"
        ? "Backend unreachable"
        : "Checking backend...";

  const dotClass =
    status === "ok"
      ? "bg-emerald-400"
      : status === "error"
        ? "bg-red-400"
        : "bg-slate-500 animate-pulse";

  return (
    <div className="mt-8 flex items-center gap-2 text-sm text-slate-500">
      <span className={`inline-block h-2 w-2 rounded-full ${dotClass}`} />
      <span>{label}</span>
    </div>
  );
}

function MarketMesh() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_78%_34%,rgba(64,92,255,0.16),transparent_26rem)]" />
      <div className="absolute bottom-[-8rem] right-[-10rem] h-[34rem] w-[72rem] rotate-[-8deg] rounded-[50%] border border-blue-400/10 bg-[radial-gradient(circle,rgba(99,102,241,0.34)_1px,transparent_1.4px)] [background-size:18px_18px] opacity-40 blur-[0.2px]" />
      <div className="absolute bottom-[-5rem] right-[-8rem] h-[22rem] w-[62rem] rotate-[-6deg] rounded-[50%] border-t border-cyan-300/20 bg-[linear-gradient(120deg,transparent,rgba(80,120,255,0.10),transparent)] blur-sm" />
      <div className="absolute bottom-[3rem] right-[4rem] h-[12rem] w-[46rem] rotate-[-7deg] rounded-[50%] border-t border-violet-300/30 opacity-80" />
    </div>
  );
}