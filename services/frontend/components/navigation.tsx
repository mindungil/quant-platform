"use client";

import Link from "next/link";

import { clearToken, hasRole } from "../lib/api";

const baseLinks = [
  ["/dashboard", "Dashboard"],
  ["/signals", "Signals"],
  ["/feed", "Feed"],
  ["/strategies", "Strategies"],
  ["/settings", "Settings"]
];

export function Navigation() {
  const links = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "Admin"]);
  }

  return (
    <nav className="flex flex-wrap items-center gap-3 text-sm">
      {links.map(([href, label]) => (
        <Link key={href} href={href} className="rounded-full border border-white/10 px-4 py-2 hover:bg-white/10">
          {label}
        </Link>
      ))}
      <button
        className="rounded-full border border-red-400/30 px-4 py-2 text-red-200 hover:bg-red-500/10"
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
