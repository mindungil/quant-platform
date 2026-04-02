"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";

import { clearToken, hasRole } from "../lib/api";

const baseLinks = [
  ["/dashboard", "대시보드"],
  ["/agent", "에이전트"],
  ["/performance", "성과"],
  ["/orders", "주문 이력"],
  ["/settings", "설정"],
];

export function Navigation() {
  const pathname = usePathname();
  const links = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "관리"]);
  }

  return (
    <nav className="flex flex-wrap items-center gap-1 text-sm">
      {links.map(([href, label]) => {
        const isActive = pathname === href || pathname?.startsWith(href + "/");
        return (
          <Link
            key={href}
            href={href}
            className={`relative rounded px-3 py-2 transition-colors ${
              isActive
                ? "font-medium text-white"
                : "text-neutral-400 hover:text-white"
            }`}
          >
            {label}
            {isActive && (
              <motion.div
                layoutId="nav-indicator"
                className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-white"
                transition={{ type: "spring", stiffness: 380, damping: 30 }}
              />
            )}
          </Link>
        );
      })}
      <button
        className="ml-2 rounded border border-neutral-700 px-3 py-1.5 text-sm text-neutral-400 transition-colors hover:border-red-400/50 hover:text-red-400"
        onClick={() => {
          clearToken();
          window.location.href = "/";
        }}
      >
        로그아웃
      </button>
    </nav>
  );
}
