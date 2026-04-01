"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { clearToken, hasRole } from "../lib/api";

const baseLinks = [
  ["/dashboard", "Dashboard"],
  ["/signals", "Signals"],
  ["/strategies", "Strategies"],
  ["/orders", "Orders"],
  ["/feed", "Feed"],
  ["/settings", "Settings"],
];

export function Navigation() {
  const pathname = usePathname();
  const links = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "Admin"]);
  }

  return (
    <nav className="flex flex-wrap items-center gap-1 text-sm">
      {links.map(([href, label]) => {
        const isActive = pathname === href || pathname?.startsWith(href + "/");
        return (
          <Link
            key={href}
            href={href}
            className={`rounded-lg px-3 py-2 transition ${
              isActive
                ? "bg-neutral-100 font-medium text-neutral-900"
                : "text-neutral-500 hover:bg-neutral-50 hover:text-neutral-900"
            }`}
          >
            {label}
          </Link>
        );
      })}
      <button
        className="ml-2 rounded-lg border border-neutral-200 px-3 py-2 text-sm text-neutral-500 transition hover:border-red-300 hover:text-red-600"
        onClick={() => {
          clearToken();
          window.location.href = "/";
        }}
      >
        Logout
      </button>
    </nav>
  );
}
