"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Navigation } from "./navigation";
import { ConnectionStatus } from "./connection-status";
import { TickerTape } from "./ticker-tape";

const NO_SHELL_ROUTES = ["/login", "/intro"];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const hideShell = NO_SHELL_ROUTES.some(
    (route) => pathname === route || pathname?.startsWith(route + "/")
  );

  if (hideShell) {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen">
      {/* ─── MASTHEAD ──────────────────────────────────────── */}
      <header className="sticky top-0 z-50 border-b border-rule bg-ink/95 backdrop-blur-md">
        {/* Brand row */}
        <div className="mx-auto flex h-14 max-w-[1400px] items-center justify-between gap-4 px-5 sm:px-8">
          <a href="/dashboard" className="group flex items-center gap-3">
            {/* Mono wordmark — bracketed terminal-style */}
            <span className="flex items-baseline gap-2">
              <span className="font-mono text-[10px] tracking-[0.2em] text-amber">[</span>
              <span className="font-mono text-[15px] font-bold tracking-[0.18em] text-paper uppercase">
                quant
              </span>
              <span className="font-mono text-[10px] tracking-[0.2em] text-amber">]</span>
              <span className="amber-led-static ml-1" aria-hidden />
            </span>
            <span className="hidden sm:inline-block label-eyebrow border-l border-rule-loud pl-3">
              v4.5 // half-kelly
            </span>
          </a>

          <div className="flex items-center gap-4">
            <div className="hidden md:block">
              <ConnectionStatus />
            </div>
            <Navigation />
          </div>
        </div>

        {/* Live ticker tape */}
        <TickerTape />
      </header>

      {/* ─── BODY ──────────────────────────────────────────── */}
      <main className="mx-auto max-w-[1400px] px-5 sm:px-8 py-8 sm:py-12">
        {children}
      </main>

      {/* ─── COLOPHON ──────────────────────────────────────── */}
      <footer className="mx-auto max-w-[1400px] px-5 sm:px-8 pb-8">
        <div className="border-t border-rule pt-5 flex flex-col sm:flex-row items-baseline justify-between gap-3">
          <p className="label-eyebrow">
            QUANT // Algorithmic Edition // v4.5
          </p>
          <p className="font-mono text-[10px] tracking-[0.16em] uppercase text-paper-low">
            Half-Kelly · Walk-Forward · Half-life 21d
          </p>
        </div>
      </footer>
    </div>
  );
}
