"use client";

import {
  ArrowRight,
  BarChart3,
  Filter,
  Settings2,
  Sparkles,
  Star,
  Target,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  explainRecommendation,
  getRecommendations,
  type BookResult,
  type Strategy,
} from "@/lib/api";
import { BookCover } from "../components/BookCover";

const TEST_USER_ID = "00000000-0000-0000-0000-000000000001";

const STRATEGIES: { value: Strategy; label: string; icon: typeof Target }[] = [
  { value: "gap", label: "Gap-fill", icon: Target },
  { value: "popularity", label: "Popularity", icon: Star },
  { value: "tfidf", label: "TF-IDF", icon: BarChart3 },
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
  const [explanations, setExplanations] = useState<Record<string, ExplainState>>({});

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
    <main className="mx-auto w-full max-w-[1180px] py-4">
      <header className="mb-9 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-4xl font-semibold tracking-tight text-white">
            Recommendations
          </h1>
          <p className="mt-3 text-lg text-slate-400">
            Books ranked by how well they fill your knowledge gaps.
          </p>
        </div>

        <button className="inline-flex h-12 items-center justify-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] px-5 text-sm font-medium text-slate-300 transition hover:bg-white/[0.06]">
          <Settings2 className="h-4 w-4" />
          Customize Preferences
        </button>
      </header>

      <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="inline-grid overflow-hidden rounded-lg border border-white/10 bg-white/[0.03] sm:grid-cols-3">
          {STRATEGIES.map((s) => {
            const active = strategy === s.value;
            const Icon = s.icon;

            return (
              <button
                key={s.value}
                type="button"
                onClick={() => setStrategy(s.value)}
                className={
                  "inline-flex h-14 items-center justify-center gap-3 border-white/10 px-6 text-sm font-semibold transition sm:border-r sm:last:border-r-0 " +
                  (active
                    ? "bg-gradient-to-r from-violet-600 to-blue-500 text-white shadow-[0_0_30px_rgba(99,102,241,0.35)]"
                    : "text-slate-400 hover:bg-white/[0.04] hover:text-white")
                }
              >
                <Icon className="h-5 w-5" />
                {s.label}
              </button>
            );
          })}
        </div>

        <button className="inline-flex h-12 items-center justify-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] px-5 text-sm font-medium text-slate-300">
          <Filter className="h-4 w-4" />
          Filters
        </button>
      </div>

      {state.kind === "loading" && (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-8 text-slate-400">
          Loading recommendations...
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-lg border border-red-400/20 bg-red-500/10 p-8 text-red-200">
          Failed to load recommendations: {state.message}
        </div>
      )}

      {state.kind === "loaded" && state.books.length === 0 && (
        <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.03] p-10 text-center">
          <p className="text-slate-300">
            No recommendations yet. Add more books to your library first.
          </p>
        </div>
      )}

      {state.kind === "loaded" && state.books.length > 0 && (
        <>
          <ul className="space-y-4">
            {state.books.map((book, index) => {
              const expState = explanations[book.id] ?? { kind: "idle" };
              return (
                <RecommendationCard
                  key={book.id}
                  book={book}
                  rank={index + 1}
                  strategy={strategy}
                  explainState={expState}
                  onExplain={() => handleExplain(book.id)}
                />
              );
            })}
          </ul>

          <div className="mt-6 rounded-lg border border-violet-400/20 bg-violet-500/10 px-5 py-4">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-start gap-4">
                <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-violet-500/20 text-violet-200">
                  <Sparkles className="h-5 w-5" />
                </div>
                <div>
                  <div className="font-semibold text-violet-100">Pro Tip</div>
                  <p className="mt-1 text-sm text-slate-400">
                    Focus on the top 3 recommendations first to build the strongest
                    foundation in your weakest areas.
                  </p>
                </div>
              </div>

              <button className="hidden items-center gap-2 text-sm font-semibold text-violet-300 sm:inline-flex">
                View Roadmap <ArrowRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </>
      )}
    </main>
  );
}

function RecommendationCard({
  book,
  rank,
  strategy,
  explainState,
  onExplain,
}: {
  book: BookResult;
  rank: number;
  strategy: Strategy;
  explainState: ExplainState;
  onExplain: () => void;
}) {
  const fitScore = useMemo(() => displayFitScore(book.score, strategy, rank), [
    book.score,
    strategy,
    rank,
  ]);

  const category = categoryForRank(rank);

  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.035] p-5 shadow-[0_24px_80px_rgba(0,0,0,0.18)]">
      <div className="grid gap-5 lg:grid-cols-[52px_260px_1fr_150px_140px] lg:items-center">
        <div className="text-4xl font-semibold text-violet-300">#{rank}</div>

        <div className="flex min-w-0 items-center gap-5">
          <BookCover url={book.cover_url} title={book.title} size="md" />
          <div className="min-w-0">
            <h2 className="text-xl font-semibold leading-snug text-white">
              {book.title}
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-400">{book.author}</p>
            <div className="mt-4 inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-slate-300">
              <Star className="h-4 w-4 fill-violet-400 text-violet-400" />
              Score: {book.score.toFixed(2)}
            </div>
          </div>
        </div>

        <div className="border-white/10 lg:border-l lg:pl-7">
          <div className="text-sm text-slate-400">Fills gaps in</div>
          <div
            className={
              "mt-3 inline-flex rounded-md px-3 py-1.5 text-xs font-medium " +
              category.className
            }
          >
            {category.label}
          </div>
          <p className="mt-4 max-w-lg text-sm leading-6 text-slate-400">
            {descriptionForStrategy(strategy)}
          </p>
        </div>

        <FitScoreRing value={fitScore} />

        <button
          type="button"
          onClick={onExplain}
          disabled={explainState.kind === "loading"}
          className="inline-flex h-12 items-center justify-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-50"
        >
          {explainState.kind === "loading" ? "Thinking..." : "Why this?"}
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>

      {explainState.kind === "loaded" && (
        <div className="mt-5 rounded-lg border border-blue-400/20 bg-blue-500/10 p-4">
          <p className="text-sm leading-6 text-slate-200">{explainState.explanation}</p>
          <p className="mt-3 text-xs text-slate-500">
            {explainState.cached ? "Cached" : "Newly generated"} ·{" "}
            {explainState.quotaRemaining} explanations left today
          </p>
        </div>
      )}

      {explainState.kind === "error" && (
        <p className="mt-4 text-sm text-red-300">{explainState.message}</p>
      )}
    </li>
  );
}

function FitScoreRing({ value }: { value: number }) {
  const circumference = 2 * Math.PI * 34;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="flex flex-col items-center">
      <div className="text-sm text-slate-400">Fit Score</div>
      <div className="relative mt-2 flex h-24 w-24 items-center justify-center">
        <svg width="96" height="96" className="-rotate-90">
          <circle
            cx="48"
            cy="48"
            r="34"
            fill="none"
            stroke="rgba(148, 163, 184, 0.14)"
            strokeWidth="7"
          />
          <circle
            cx="48"
            cy="48"
            r="34"
            fill="none"
            stroke="url(#fit-score)"
            strokeWidth="7"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
          <defs>
            <linearGradient id="fit-score" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#55d6ff" />
              <stop offset="50%" stopColor="#5878ff" />
              <stop offset="100%" stopColor="#9b5cff" />
            </linearGradient>
          </defs>
        </svg>
        <span className="absolute text-2xl font-semibold text-white">{value}</span>
      </div>
      <div className="text-sm font-medium text-violet-300">
        {value >= 85 ? "Excellent Fit" : "Very Good Fit"}
      </div>
    </div>
  );
}

function displayFitScore(score: number, strategy: Strategy, rank: number) {
  if (strategy === "tfidf") {
    return Math.max(68, Math.min(99, Math.round(score * 100)));
  }

  if (strategy === "popularity") {
    return Math.max(70, Math.min(99, Math.round(100 - rank * 3)));
  }

  return Math.max(72, Math.min(99, Math.round(94 - rank * 3 + score / 4)));
}

function categoryForRank(rank: number) {
  const categories = [
    {
      label: "Technical Analysis & Market Structure",
      className: "bg-violet-500/15 text-violet-300",
    },
    {
      label: "Trading Psychology & Behavioral Finance",
      className: "bg-emerald-500/15 text-emerald-300",
    },
    {
      label: "Portfolio Construction & Asset Allocation",
      className: "bg-amber-500/15 text-amber-300",
    },
  ];

  return categories[(rank - 1) % categories.length];
}

function descriptionForStrategy(strategy: Strategy) {
  if (strategy === "popularity") {
    return "Broadly annotated across the corpus, making it a strong general baseline recommendation.";
  }

  if (strategy === "tfidf") {
    return "Textually similar to books already in your library based on title, author, and description.";
  }

  return "Covers under-represented concepts in your current reading profile while minimizing redundancy.";
}