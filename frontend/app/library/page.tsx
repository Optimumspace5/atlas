"use client";

import Link from "next/link";
import { Grid2X2, List, MoreVertical, Plus, Search, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  getConcepts,
  getUserBooks,
  getUserCoverage,
  type BookSearchResult,
  type ConceptParent,
  type CoverageResponse,
} from "@/lib/api";
import { BookCover } from "../components/BookCover";
import { ProgressRing } from "../components/ProgressRing";

const TEST_USER_ID = "00000000-0000-0000-0000-000000000001";
const COVERAGE_TARGET = 2.0;

type LoadState =
  | { kind: "loading" }
  | {
      kind: "loaded";
      books: BookSearchResult[];
      concepts: ConceptParent[];
      coverage: CoverageResponse;
    }
  | { kind: "error"; message: string };

export default function LibraryPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [query, setQuery] = useState("");

  useEffect(() => {
    Promise.all([
      getUserBooks(TEST_USER_ID),
      getConcepts(),
      getUserCoverage(TEST_USER_ID),
    ])
      .then(([books, concepts, coverage]) =>
        setState({ kind: "loaded", books, concepts, coverage }),
      )
      .catch((e) =>
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : "Unknown error",
        }),
      );
  }, []);

  const filteredBooks = useMemo(() => {
    if (state.kind !== "loaded") {
      return [];
    }

    const trimmed = query.trim().toLowerCase();
    if (!trimmed) {
      return state.books;
    }

    return state.books.filter((book) => {
      return (
        book.title.toLowerCase().includes(trimmed) ||
        book.author.toLowerCase().includes(trimmed)
      );
    });
  }, [query, state]);

  return (
    <main className="mx-auto w-full max-w-[1360px] py-4 lg:px-4">
      <header className="mb-9 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-4xl font-semibold tracking-tight text-white">
            Your Library
          </h1>
          <p className="mt-3 text-lg text-slate-400">
            Track your reading history and concept coverage.
          </p>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search your books..."
              className="h-14 w-full rounded-lg border border-white/10 bg-white/[0.03] pl-12 pr-4 text-sm text-white outline-none placeholder:text-slate-500 transition focus:border-violet-400/70 sm:w-80"
            />
          </div>

          <Link
            href="/"
            className="inline-flex h-14 items-center justify-center gap-2 rounded-lg border border-violet-400/50 bg-violet-500/10 px-5 text-sm font-semibold text-violet-200 transition hover:bg-violet-500/20"
          >
            <Plus className="h-5 w-5" />
            Add Book
          </Link>
        </div>
      </header>

      {state.kind === "loading" && (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-8 text-slate-400">
          Loading library...
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-lg border border-red-400/20 bg-red-500/10 p-8 text-red-200">
          Failed to load library: {state.message}
        </div>
      )}

      {state.kind === "loaded" && (
        <>
          <LibraryCoveragePanel
            concepts={state.concepts}
            coverage={state.coverage}
          />

          <section className="mt-9">
            <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-3">
                <h2 className="text-2xl font-semibold tracking-tight text-white">
                  Books You&apos;ve Logged
                </h2>
                <span className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-1 text-sm text-slate-400">
                  {state.books.length} books
                </span>
              </div>

              <div className="flex items-center gap-3">
                <button className="inline-flex h-12 items-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] px-4 text-sm text-slate-300">
                  <SlidersHorizontal className="h-4 w-4" />
                  Recently Added
                </button>
                <button className="flex h-12 w-12 items-center justify-center rounded-lg border border-white/10 bg-white/[0.03] text-slate-400">
                  <Grid2X2 className="h-5 w-5" />
                </button>
                <button className="flex h-12 w-12 items-center justify-center rounded-lg border border-violet-400/50 bg-violet-500/10 text-violet-200">
                  <List className="h-5 w-5" />
                </button>
              </div>
            </div>

            {filteredBooks.length === 0 ? (
              <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.03] p-10 text-center">
                <p className="text-slate-300">
                  {state.books.length === 0
                    ? "Your library is empty."
                    : "No books match your search."}
                </p>
                <Link
                  href="/"
                  className="mt-4 inline-flex text-sm font-medium text-violet-300"
                >
                  Add a book
                </Link>
              </div>
            ) : (
              <ul className="overflow-hidden rounded-lg border border-white/10 bg-white/[0.03]">
                {filteredBooks.map((book, index) => (
                  <LibraryRow key={book.id} book={book} index={index} />
                ))}
              </ul>
            )}

            {filteredBooks.length > 0 && (
              <div className="mt-7 flex items-center justify-center gap-2 text-sm text-slate-500">
                <span className="h-5 w-5 rounded border border-white/10" />
                No more books to show
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}

function LibraryCoveragePanel({
  concepts,
  coverage,
}: {
  concepts: ConceptParent[];
  coverage: CoverageResponse;
}) {
  const percent = Math.round((coverage.covered_count / 48) * 100);

  const rows = concepts.map((parent) => {
    const leafScores = parent.leaves.map((leaf) => coverage.coverage[leaf.slug] ?? 0);
    const filledLeaves = leafScores.filter((score) => score > 0).length;
    const normalized =
      leafScores.reduce((sum, score) => sum + Math.min(score, COVERAGE_TARGET), 0) /
      Math.max(1, parent.leaves.length * COVERAGE_TARGET);

    return {
      name: parent.name,
      covered: filledLeaves,
      total: parent.leaves.length,
      percent: Math.round(normalized * 100),
    };
  });

  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.035] p-8 shadow-[0_24px_80px_rgba(0,0,0,0.22)]">
      <div className="mb-7 flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white">Concept Coverage</h2>
          <p className="mt-2 text-sm text-slate-400">
            How filled is your knowledge map?
          </p>
        </div>

        <div className="hidden grid-cols-[80px_80px] gap-10 text-sm text-slate-400 sm:grid">
          <span>Covered</span>
          <span>% Filled</span>
        </div>
      </div>

      <div className="grid gap-10 lg:grid-cols-[300px_1fr] lg:items-center">
        <div className="flex justify-center">
          <ProgressRing
            value={percent}
            size={210}
            strokeWidth={13}
            label="Overall Coverage"
          />
        </div>

        <div className="space-y-5">
          {rows.map((row, index) => (
            <div
              key={row.name}
              className="grid gap-3 sm:grid-cols-[minmax(220px,1fr)_minmax(220px,1fr)_80px_70px] sm:items-center"
            >
              <div className="text-sm text-slate-200">{row.name}</div>
              <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
                <div
                  className={
                    "h-full rounded-full " +
                    (index === 3
                      ? "bg-cyan-300"
                      : "bg-gradient-to-r from-violet-400 to-blue-400")
                  }
                  style={{ width: `${row.percent}%` }}
                />
              </div>
              <div className="text-sm text-slate-300">
                {row.covered} / {row.total}
              </div>
              <div className="text-sm text-slate-300">{row.percent}%</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function LibraryRow({
  book,
  index,
}: {
  book: BookSearchResult;
  index: number;
}) {
  const tags = [
    "Market Foundations",
    "Risk Management",
    "Portfolio Construction",
    "Behavioral Finance",
    "Technical Analysis",
  ];

  const tag = tags[index % tags.length];

  return (
    <li className="grid gap-4 border-b border-white/8 px-5 py-5 last:border-b-0 lg:grid-cols-[minmax(260px,1.3fr)_minmax(220px,1fr)_220px_120px_44px] lg:items-center">
      <div className="flex min-w-0 items-center gap-4">
        <BookCover url={book.cover_url} title={book.title} size="sm" />
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-white">{book.title}</h3>
          <p className="mt-1 truncate text-sm text-slate-400">{book.author}</p>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <span className="text-sm text-slate-400">Completed</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
          <div className="h-full w-full rounded-full bg-gradient-to-r from-violet-400 to-blue-400" />
        </div>
        <span className="text-sm text-slate-400">100%</span>
      </div>

      <div>
        <span
          className={
            "inline-flex rounded-md px-3 py-1.5 text-xs font-medium " +
            (tag.includes("Behavioral")
              ? "bg-blue-500/15 text-blue-300"
              : tag.includes("Portfolio")
                ? "bg-cyan-500/15 text-cyan-300"
                : tag.includes("Risk")
                  ? "bg-violet-500/15 text-violet-300"
                  : "bg-purple-500/15 text-purple-300")
          }
        >
          {tag}
        </span>
      </div>

      <div className="text-sm text-slate-400">
        {book.publication_year ? `Read ${book.publication_year}` : "Recently added"}
      </div>

      <button className="flex h-10 w-10 items-center justify-center rounded-md border border-white/10 text-slate-400">
        <MoreVertical className="h-4 w-4" />
      </button>
    </li>
  );
}