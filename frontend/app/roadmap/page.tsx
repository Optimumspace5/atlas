"use client";

import { ArrowDown, Check, LineChart, Plus, Sparkles, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";
import {
  addMyBook,
  getMyRoadmap,
  type RoadmapResponse,
  type RoadmapRole,
  type RoadmapRung,
} from "@/lib/api";
import { BookCover } from "../components/BookCover";

const ROLES: { value: RoadmapRole; label: string; icon: typeof TrendingUp; blurb: string }[] = [
  {
    value: "investor",
    label: "Long-Term Investor",
    icon: TrendingUp,
    blurb: "A reading journey from index-fund foundations to value, cycles, and tail risk.",
  },
  {
    value: "trader",
    label: "Trader",
    icon: LineChart,
    blurb: "From chart-reading foundations through psychology, systems, and market microstructure.",
  },
];

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; data: RoadmapResponse }
  | { kind: "error"; message: string };

export default function RoadmapPage() {
  const [role, setRole] = useState<RoadmapRole>("investor");
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    getMyRoadmap(role)
      .then((data) => active && setState({ kind: "loaded", data }))
      .catch(
        (e) =>
          active &&
          setState({
            kind: "error",
            message: e instanceof Error ? e.message : "Unknown error",
          }),
      );
    return () => {
      active = false;
    };
  }, [role]);

  async function markRead(bookId: string) {
    setPending(bookId);
    try {
      await addMyBook(bookId);
      const data = await getMyRoadmap(role);
      setState({ kind: "loaded", data });
    } catch {
      // leave state as-is; the button re-enables
    } finally {
      setPending(null);
    }
  }

  const active = ROLES.find((r) => r.value === role)!;
  const data = state.kind === "loaded" ? state.data : null;
  const percent = data && data.total ? Math.round((data.read_count / data.total) * 100) : 0;

  return (
    <main className="mx-auto w-full max-w-[920px] py-4">
      <header className="mb-8">
        <h1 className="text-4xl font-semibold tracking-tight text-white">Roadmap</h1>
        <p className="mt-3 text-lg text-slate-400">{active.blurb}</p>
      </header>

      {/* role toggle */}
      <div className="mb-8 inline-grid w-full overflow-hidden rounded-lg border border-white/10 bg-white/[0.03] sm:w-auto sm:grid-cols-2">
        {ROLES.map((r) => {
          const on = role === r.value;
          const Icon = r.icon;
          return (
            <button
              key={r.value}
              type="button"
              onClick={() => setRole(r.value)}
              className={
                "inline-flex h-14 items-center justify-center gap-3 px-8 text-sm font-semibold transition sm:border-r sm:border-white/10 sm:last:border-r-0 " +
                (on
                  ? "bg-gradient-to-r from-violet-600 to-blue-500 text-white shadow-[0_0_30px_rgba(99,102,241,0.35)]"
                  : "text-slate-400 hover:bg-white/[0.04] hover:text-white")
              }
            >
              <Icon className="h-5 w-5" />
              {r.label}
            </button>
          );
        })}
      </div>

      {data && (
        <div className="mb-8 flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.03] px-5 py-4">
          <div className="text-sm text-slate-300">
            <span className="font-semibold text-white">{data.read_count}</span> of{" "}
            {data.total} read
          </div>
          <div className="h-2 w-40 overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-violet-500 transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      )}

      {state.kind === "loading" && (
        <div className="rounded-lg border border-white/10 bg-white/[0.03] p-8 text-slate-400">
          Loading roadmap...
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-lg border border-red-400/20 bg-red-500/10 p-8 text-red-200">
          Failed to load roadmap: {state.message}
        </div>
      )}

      {data && (
        <ol className="relative space-y-5">
          {data.rungs.map((rung, i) => (
            <RungCard
              key={`${role}-${rung.rung}`}
              rung={rung}
              isLast={i === data.rungs.length - 1}
              pending={pending === rung.book_id}
              onMarkRead={() => rung.book_id && markRead(rung.book_id)}
            />
          ))}
        </ol>
      )}
    </main>
  );
}

function RungCard({
  rung,
  isLast,
  pending,
  onMarkRead,
}: {
  rung: RoadmapRung;
  isLast: boolean;
  pending: boolean;
  onMarkRead: () => void;
}) {
  const cardClass = rung.read
    ? "border-emerald-400/30 bg-emerald-500/[0.06]"
    : rung.is_next
      ? "border-violet-400/60 bg-violet-500/[0.08] shadow-[0_0_40px_rgba(99,102,241,0.18)]"
      : "border-white/10 bg-white/[0.035]";

  return (
    <li className="relative pl-14">
      {/* rail node + connector */}
      <div
        className={
          "absolute left-3 top-5 z-10 flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold " +
          (rung.read
            ? "bg-emerald-500 text-white"
            : rung.is_next
              ? "bg-gradient-to-br from-violet-500 to-blue-500 text-white"
              : "border border-white/15 bg-[#0a0f1c] text-slate-400")
        }
      >
        {rung.read ? <Check className="h-4 w-4" /> : rung.rung}
      </div>
      {!isLast && (
        <span className="absolute left-[25px] top-12 h-[calc(100%-1rem)] w-px bg-white/10" />
      )}

      <div className={"rounded-lg border p-5 transition " + cardClass}>
        {rung.is_next && !rung.read && (
          <div className="mb-3 inline-flex items-center gap-2 rounded-md bg-violet-500/20 px-3 py-1 text-xs font-semibold text-violet-200">
            <Sparkles className="h-3.5 w-3.5" /> Read this next
          </div>
        )}

        <div className="flex gap-5">
          <BookCover url={rung.cover_url} title={rung.title} size="md" />

          <div className="min-w-0 flex-1">
            <h2 className="text-xl font-semibold leading-snug text-white">{rung.title}</h2>
            <p className="mt-1 text-sm text-slate-400">{rung.author}</p>
            {rung.why && (
              <p className="mt-3 text-sm leading-6 text-slate-300">{rung.why}</p>
            )}

            {rung.new_concepts.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {rung.new_concepts.slice(0, 6).map((c) => (
                  <span
                    key={c}
                    className="rounded-md bg-white/[0.05] px-2.5 py-1 text-xs text-slate-400"
                  >
                    {c.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            )}

            <div className="mt-4">
              {rung.read ? (
                <span className="inline-flex items-center gap-2 text-sm font-medium text-emerald-300">
                  <Check className="h-4 w-4" /> In your library
                </span>
              ) : (
                <button
                  type="button"
                  onClick={onMarkRead}
                  disabled={pending || !rung.book_id}
                  className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.08] disabled:opacity-50"
                >
                  <Plus className="h-4 w-4" />
                  {pending ? "Adding..." : "Mark as read"}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {!isLast && (
        <div className="flex justify-center py-1 text-slate-600">
          <ArrowDown className="h-4 w-4" />
        </div>
      )}
    </li>
  );
}
