"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";

type HealthStatus = "checking" | "ok" | "error";

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [health, setHealth] = useState<HealthStatus>("checking");

  useEffect(() => {
    getHealth()
      .then((res) => setHealth(res.status === "ok" ? "ok" : "error"))
      .catch(() => setHealth("error"));
  }, []);

  function handleAdd() {
    // Placeholder — Google Books autocomplete + POST /users/{id}/books wires in later.
    console.log("add:", query);
  }

  return (
    <main className="min-h-screen bg-gray-50 flex flex-col items-center p-8">
      <header className="w-full max-w-2xl mt-12 mb-8">
        <h1 className="text-4xl font-bold text-gray-900">Atlas</h1>
        <p className="text-gray-600 mt-2">
          Knowledge-gap-aware book recommendations for investing and trading.
        </p>
      </header>

      <section className="w-full max-w-2xl">
        <label htmlFor="search" className="block text-sm font-medium text-gray-700 mb-2">
          Add a book to your library
        </label>
        <div className="flex gap-2">
          <input
            id="search"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by title or author…"
            className="flex-1 border border-gray-300 rounded-lg p-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={handleAdd}
            disabled={!query.trim()}
            className="bg-blue-600 text-white rounded-lg px-4 py-2 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
          >
            Add
          </button>
        </div>
        <p className="text-xs text-gray-500 mt-2">
          Search is a placeholder — Google Books autocomplete wires in next.
        </p>
      </section>

      <footer className="w-full max-w-2xl mt-auto pt-12 text-sm">
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
