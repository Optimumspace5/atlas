"use client";

import {
  getConcepts,
  getUserCoverage,
  type ConceptParent,
  type CoverageResponse,
} from "@/lib/api";
import { useEffect, useState } from "react";


// Matches COVERAGE_TARGET in backend/app/services/explanation.py.
// Coverage at or above this is rendered as "filled" (green).
const COVERAGE_TARGET = 2.0;


type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; concepts: ConceptParent[]; coverage: CoverageResponse }
  | { kind: "error"; message: string };


export function CoverageChart({ userId }: { userId: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    Promise.all([getConcepts(), getUserCoverage(userId)])
      .then(([concepts, coverage]) =>
        setState({ kind: "loaded", concepts, coverage }),
      )
      .catch((e) =>
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : "Unknown error",
        }),
      );
  }, [userId]);

  if (state.kind === "loading") {
    return <div className="text-sm text-gray-500">Loading coverage…</div>;
  }
  if (state.kind === "error") {
    return (
      <div className="text-sm text-red-700">
        Failed to load coverage: {state.message}
      </div>
    );
  }

  const { concepts, coverage } = state;

  return (
    <section className="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
      <div className="flex items-end justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Concept Coverage</h2>
          <p className="text-sm text-gray-600">
            How filled your reading profile is, grouped by category.
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold text-gray-900">
            {coverage.covered_count}
            <span className="text-gray-400">/48</span>
          </div>
          <div className="text-xs text-gray-500">concepts touched</div>
        </div>
      </div>

      <div className="space-y-5">
        {concepts.map((parent) => (
          <ParentSection
            key={parent.slug}
            parent={parent}
            coverage={coverage.coverage}
          />
        ))}
      </div>
    </section>
  );
}


function ParentSection({
  parent,
  coverage,
}: {
  parent: ConceptParent;
  coverage: Record<string, number>;
}) {
  // Aggregate: average normalized coverage across this parent's leaves.
  const leafScores = parent.leaves.map((l) => coverage[l.slug] ?? 0);
  const aggregateNormalized =
    leafScores.reduce((sum, s) => sum + Math.min(s, COVERAGE_TARGET), 0) /
    (parent.leaves.length * COVERAGE_TARGET);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <h3 className="text-sm font-semibold text-gray-800">{parent.name}</h3>
        <span className="text-xs text-gray-500 font-mono">
          {(aggregateNormalized * 100).toFixed(0)}%
        </span>
      </div>
      <Bar normalized={aggregateNormalized} height="h-2.5" />

      <ul className="mt-2 space-y-1">
        {parent.leaves.map((leaf) => {
          const score = coverage[leaf.slug] ?? 0;
          const normalized = Math.min(score, COVERAGE_TARGET) / COVERAGE_TARGET;
          return (
            <li key={leaf.slug} className="flex items-center gap-3">
              <span className="flex-1 text-xs text-gray-600 truncate">
                {leaf.name}
              </span>
              <div className="w-32 sm:w-44">
                <Bar normalized={normalized} height="h-1.5" />
              </div>
              <span className="w-10 text-right text-xs font-mono text-gray-400">
                {score.toFixed(1)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}


function Bar({
  normalized,
  height,
}: {
  normalized: number;
  height: string;
}) {
  const pct = Math.min(100, Math.max(0, normalized * 100));

  // Color buckets: zero -> gray, partial -> blue, full -> green
  const colorClass =
    normalized === 0 ? "bg-gray-200"
    : normalized >= 1 ? "bg-emerald-500"
    : "bg-blue-500";

  return (
    <div className={`w-full ${height} bg-gray-100 rounded-full overflow-hidden`}>
      <div
        className={`${height} ${colorClass} rounded-full transition-all`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
