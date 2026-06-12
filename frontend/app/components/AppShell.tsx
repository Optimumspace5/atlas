"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowRight, Bell, BookOpen, Home, Library, Sparkles, Target, UserCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { getMyCoverage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ProgressRing } from "./ProgressRing";

const navItems = [
  { href: "/", label: "Home", icon: Home },
  { href: "/library", label: "Library", icon: Library },
  { href: "/recommendations", label: "Recommendations", icon: Target },
  { href: "/roadmap", label: "Roadmap", icon: BookOpen },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user && pathname !== "/login") {
      router.replace("/login");
    }
  }, [loading, user, pathname, router]);

  // Initial session check — don't flash a redirect while we don't know yet.
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#050812] text-slate-400">
        Loading...
      </div>
    );
  }

  // Unauthenticated on a protected route — render nothing while redirecting.
  if (!user && pathname !== "/login") {
    return null;
  }

  return (
    <div className="min-h-screen bg-[#050812] text-slate-100">
      <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[296px_1fr]">
        <Sidebar />
        <section className="relative min-w-0">
          <TopActions />
          <main className="w-full px-5 py-8 sm:px-8 lg:px-12">
            {children}
          </main>
        </section>
      </div>
    </div>
  );
}

function Sidebar() {
  const pathname = usePathname();
  const { user, signOut } = useAuth();
  const [covered, setCovered] = useState(0);

  useEffect(() => {
    if (!user) return;
    getMyCoverage()
      .then((coverage) => setCovered(coverage.covered_count))
      .catch(() => setCovered(0));
  }, [user]);

  const percent = Math.round((covered / 48) * 100);

  return (
    <aside className="border-b border-white/10 bg-[#070b15]/95 px-5 py-6 lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
      <div className="flex h-full flex-col">
        <Link href="/" className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 via-blue-500 to-violet-500 text-4xl font-black text-white shadow-[0_0_32px_rgba(99,102,241,0.35)]">
            A
          </div>
          <div>
            <div className="text-2xl font-semibold tracking-tight">Atlas</div>
            <div className="mt-1 text-sm leading-5 text-slate-400">
              Knowledge-Gap-Aware<br />Book Recommender
            </div>
          </div>
        </Link>

        <nav className="mt-12 space-y-2">
          {navItems.map((item) => {
            const active = pathname === item.href;
            const Icon = item.icon;

            return (
              <Link
                key={item.href}
                href={item.href}
                className={
                  "flex h-14 items-center gap-4 rounded-lg border px-4 text-sm font-medium transition " +
                  (active
                    ? "border-violet-500/60 bg-white/[0.07] text-white shadow-[inset_3px_0_0_#7c5cff]"
                    : "border-transparent text-slate-400 hover:bg-white/[0.04] hover:text-white")
                }
              >
                <Icon className={active ? "h-5 w-5 text-violet-300" : "h-5 w-5"} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-10 rounded-lg border border-white/10 bg-white/[0.03] p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Your Progress</div>
          <div className="mt-6 flex justify-center">
            <ProgressRing value={percent} size={148} strokeWidth={11} />
          </div>
          <div className="mt-5 text-center text-sm text-slate-300">Overall Coverage</div>
          <div className="mt-1 text-center text-sm text-slate-400">{covered} / 48 concepts</div>
          <Link
            href="/library"
            className="mt-5 flex h-12 items-center justify-center gap-3 rounded-md border border-white/10 text-sm font-medium text-violet-300 transition hover:border-violet-400/50 hover:bg-violet-500/10"
          >
            View Library <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        <div className="mt-5 rounded-lg border border-white/10 bg-white/[0.03] p-5">
          <Sparkles className="h-5 w-5 text-violet-300" />
          <div className="mt-4 font-semibold text-white">Keep building.</div>
          <div className="mt-2 text-sm leading-6 text-slate-400">Consistency compounds.</div>
          <Link href="/roadmap" className="mt-5 flex items-center gap-2 text-sm font-medium text-violet-300">
            View Roadmap <ArrowRight className="h-4 w-4" />
          </Link>
        </div>

        {user ? (
          <div className="mt-auto flex items-center gap-3 border-t border-white/10 pt-5">
            <div className="flex h-12 w-12 items-center justify-center rounded-full border border-white/10 bg-slate-800 text-lg font-semibold uppercase">
              {user.email?.[0] ?? "U"}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium text-white">{user.email}</div>
              <button
                onClick={() => signOut()}
                className="text-sm text-violet-300 transition hover:text-violet-200"
              >
                Sign out
              </button>
            </div>
          </div>
        ) : (
          <Link
            href="/login"
            className="mt-auto flex h-12 items-center justify-center gap-2 rounded-md border border-violet-400/50 bg-violet-500/10 text-sm font-semibold text-violet-200 transition hover:bg-violet-500/20"
          >
            Sign in
          </Link>
        )}
      </div>
    </aside>
  );
}

function TopActions() {
  return (
    <div className="pointer-events-none absolute right-8 top-8 z-10 hidden gap-3 lg:flex">
      <button className="pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-slate-300">
        <Bell className="h-5 w-5" />
      </button>
      <button className="pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/[0.03] text-slate-300">
        <UserCircle className="h-5 w-5" />
      </button>
    </div>
  );
}
