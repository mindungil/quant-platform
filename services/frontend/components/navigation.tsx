"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { clearToken, hasRole } from "../lib/api";
import { startTour } from "./onboarding-tour";

const baseLinks: [string, string, string][] = [
  ["/dashboard",             "메인",        "01"],
  ["/chat",                  "에이전트",    "02"],
  ["/agent",                 "자동매매",    "03"],
  ["/monitoring/operations", "모니터링",    "04"],
  ["/strategies",            "전략",        "05"],
  ["/performance",           "성과",        "06"],
  ["/settings",              "설정",        "07"],
];

export function Navigation() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const links = [...baseLinks];
  if (hasRole("admin")) {
    links.push(["/admin", "관리", "08"]);
  }

  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  const handleLogout = () => {
    clearToken();
    window.location.href = "/login";
  };

  return (
    <>
      {/* ── DESKTOP ── */}
      <nav className="hidden md:flex items-stretch text-sm">
        {links.map(([href, label, num]) => {
          // Top-level section match: /monitoring/operations link should also
          // light up when the user is browsing /monitoring (alpha health) or
          // any other /monitoring/* sub-route.
          const sectionRoot = href.startsWith("/monitoring") ? "/monitoring" : href;
          const isActive =
            pathname === href ||
            pathname?.startsWith(href + "/") ||
            (sectionRoot !== href && (pathname === sectionRoot || pathname?.startsWith(sectionRoot + "/")));
          return (
            <Link
              key={href}
              href={href}
              data-tour={`nav-${href.replace(/^\//, "")}`}
              className={`group relative flex items-center gap-2 px-3.5 py-1.5 transition-colors ${
                isActive ? "text-amber" : "text-paper-dim hover:text-paper"
              }`}
            >
              <span className={`font-mono text-[9px] tracking-[0.18em] ${isActive ? "text-amber-deep" : "text-paper-low group-hover:text-amber-deep"}`}>
                {num}
              </span>
              <span className="font-mono text-[12px] font-medium uppercase tracking-[0.12em]">
                {label}
              </span>
              {isActive && (
                <motion.span
                  layoutId="nav-underline"
                  className="absolute -bottom-[16px] left-3.5 right-3.5 h-[2px] bg-amber"
                  style={{ boxShadow: "0 0 12px rgba(251,189,46,0.5)" }}
                  transition={{ type: "spring", stiffness: 380, damping: 32 }}
                />
              )}
            </Link>
          );
        })}
        <button
          className="ml-2 px-3 text-paper-mute font-mono text-[10px] uppercase tracking-[0.16em] transition-colors hover:text-amber"
          onClick={startTour}
          title="기능 투어"
        >
          ?
        </button>
        <button
          className="ml-1 px-3 text-paper-mute font-mono text-[10px] uppercase tracking-[0.16em] transition-colors hover:text-coral"
          onClick={handleLogout}
        >
          Logout
        </button>
      </nav>

      {/* ── MOBILE TRIGGER ── */}
      <button
        type="button"
        aria-label="메뉴 열기"
        aria-expanded={open}
        className="md:hidden inline-flex h-10 w-10 items-center justify-center border border-rule-loud text-paper transition-colors hover:border-amber hover:text-amber"
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          {open ? (<><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>)
                : (<><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></>)}
        </svg>
      </button>

      {/* ── MOBILE DRAWER ── */}
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="md:hidden fixed inset-0 top-14 z-40 bg-black/70 backdrop-blur-sm"
              onClick={() => setOpen(false)}
            />
            <motion.nav
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "tween", duration: 0.22, ease: "easeOut" }}
              className="md:hidden fixed right-0 top-14 z-50 h-[calc(100vh-3.5rem)] w-[82vw] max-w-sm overflow-y-auto border-l border-rule-loud bg-ink/98 px-6 py-7"
            >
              <p className="label-eyebrow-amber mb-5">Sections</p>
              <div className="flex flex-col">
                {links.map(([href, label, num]) => {
                  const isActive = pathname === href || pathname?.startsWith(href + "/");
                  return (
                    <Link
                      key={href}
                      href={href}
                      className={`flex items-baseline justify-between border-b border-rule py-4 transition-colors ${
                        isActive ? "text-amber" : "text-paper hover:text-amber"
                      }`}
                    >
                      <span className="font-mono text-[16px] font-medium uppercase tracking-[0.12em]">{label}</span>
                      <span className="font-mono text-[10px] tracking-[0.18em] text-paper-low">{num}</span>
                    </Link>
                  );
                })}
                <button
                  className="mt-8 self-start font-mono text-[11px] uppercase tracking-[0.18em] text-coral hover:text-paper"
                  onClick={handleLogout}
                >
                  → Logout
                </button>
              </div>
            </motion.nav>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
