"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Home" },
  { href: "/library", label: "Library" },
  { href: "/recommendations", label: "Recommendations" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="w-full max-w-2xl mt-8 mb-4">
      <ul className="flex gap-1 border-b border-gray-200">
        {LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <li key={link.href}>
              <Link
                href={link.href}
                className={
                  "inline-block px-3 py-2 text-sm font-medium border-b-2 -mb-px transition " +
                  (active
                    ? "border-blue-600 text-blue-700"
                    : "border-transparent text-gray-600 hover:text-gray-900")
                }
              >
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
