"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { clearToken, hasRole } from "../lib/api";

const baseLinks = [
  ["/dashboard", "대시보드"],
  ["/chat", "AI 에이전트"],
  ["/agent", "자동매매"],
  ["/strategies", "전략"],
  ["/performance", "성과"],
  ["/orders", "주문"],
  ["/pricing", "플랜"],
  ["/settings", "설정"],
];

export function Navigation() {
  const pathname = usePathname();
  const links = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "관리"]);
  }

  return (
    <nav className="flex items-center gap-0.5 text-sm">
      {links.map(([href, label]) => {
        const isActive = pathname === href || pathname?.startsWith(href + "/");
        return (
          <Link
            key={href}
            href={href}
            className={`relative rounded-lg px-3 py-1.5 transition-colors duration-150 ${
              isActive
                ? "font-medium text-zinc-50"
                : "text-zinc-400 hover:text-zinc-50"
            }`}
          >
            {label}
            {isActive && (
              <motion.div
                layoutId="nav-active"
                className="absolute inset-0 rounded-lg bg-white/[0.08] border border-white/[0.10]"
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                style={{ zIndex: -1 }}
              />
            )}
          </Link>
        );
      })}
      <button
        className="ml-3 rounded-lg px-2.5 py-1 text-sm text-zinc-400 transition-colors duration-150 hover:text-red-400"
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
