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
  ["/settings", "설정"],
];

export function Navigation() {
  const pathname = usePathname();
  const links = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "관리"]);
  }

  return (
    <nav className="flex items-center gap-0.5 text-[13px]">
      {links.map(([href, label]) => {
        const isActive = pathname === href || pathname?.startsWith(href + "/");
        return (
          <Link
            key={href}
            href={href}
            className={`relative rounded-lg px-3 py-1.5 transition-colors ${
              isActive
                ? "font-medium text-cyan-300"
                : "text-neutral-500 hover:text-white"
            }`}
          >
            {label}
            {isActive && (
              <motion.div
                layoutId="nav-active"
                className="absolute inset-0 rounded-lg bg-gradient-to-r from-cyan-500/10 to-cyan-500/5 border border-cyan-500/20"
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                style={{ zIndex: -1 }}
              />
            )}
          </Link>
        );
      })}
      <button
        className="ml-3 rounded-lg px-2.5 py-1 text-[13px] text-neutral-600 transition-colors hover:text-red-400"
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
