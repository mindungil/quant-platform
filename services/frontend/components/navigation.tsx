"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { clearToken, hasRole } from "../lib/api";

const baseLinks: [string, string][] = [
  ["/dashboard", "대시보드"],
  ["/chat", "AI 에이전트"],
  ["/agent", "자동매매"],
  ["/strategies", "전략"],
  ["/templates", "템플릿"],
  ["/performance", "성과"],
  ["/orders", "주문"],
  ["/pricing", "플랜"],
  ["/settings", "설정"],
];

export function Navigation() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const links: [string, string][] = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "관리"]);
  }

  // Close drawer on route change
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Lock body scroll when drawer open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  const handleLogout = () => {
    clearToken();
    window.location.href = "/";
  };

  return (
    <>
      {/* Desktop nav (md+) */}
      <nav className="hidden md:flex items-center gap-0.5 text-sm min-w-0">
        {links.map(([href, label]) => {
          const isActive = pathname === href || pathname?.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={`relative rounded-lg px-3 py-1.5 transition-colors duration-150 whitespace-nowrap ${
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
          className="ml-3 rounded-lg px-2.5 py-1 text-sm text-zinc-400 transition-colors duration-150 hover:text-red-400 whitespace-nowrap"
          onClick={handleLogout}
        >
          로그아웃
        </button>
      </nav>

      {/* Mobile hamburger button */}
      <button
        type="button"
        aria-label="메뉴 열기"
        aria-expanded={open}
        className="md:hidden inline-flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.03] text-zinc-200 transition-colors hover:bg-white/[0.06]"
        onClick={() => setOpen((v) => !v)}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {open ? (
            <>
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </>
          ) : (
            <>
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </>
          )}
        </svg>
      </button>

      {/* Mobile drawer */}
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="md:hidden fixed inset-0 top-14 z-40 bg-black/60 backdrop-blur-sm"
              onClick={() => setOpen(false)}
            />
            <motion.nav
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "tween", duration: 0.2 }}
              className="md:hidden fixed right-0 top-14 z-50 h-[calc(100vh-3.5rem)] w-[78vw] max-w-xs overflow-y-auto border-l border-white/[0.06] bg-zinc-950/95 backdrop-blur-xl px-3 py-4"
            >
              <div className="flex flex-col gap-1">
                {links.map(([href, label]) => {
                  const isActive = pathname === href || pathname?.startsWith(href + "/");
                  return (
                    <Link
                      key={href}
                      href={href}
                      className={`block truncate rounded-lg px-4 py-3 text-sm transition-colors ${
                        isActive
                          ? "bg-white/[0.08] text-zinc-50 font-medium border border-white/[0.10]"
                          : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-50"
                      }`}
                    >
                      {label}
                    </Link>
                  );
                })}
                <button
                  className="mt-2 block w-full truncate rounded-lg px-4 py-3 text-left text-sm text-red-400 transition-colors hover:bg-red-500/10"
                  onClick={handleLogout}
                >
                  로그아웃
                </button>
              </div>
            </motion.nav>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
