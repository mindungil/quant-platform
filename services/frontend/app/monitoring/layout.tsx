"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { hasRole } from "../../lib/api";

const TABS: [string, string, string][] = [
  ["/monitoring/operations", "Operations", "01"],
  ["/monitoring",            "Alpha Health", "02"],
];

const ADMIN_TABS: [string, string, string][] = [
  ["/soak", "Soak", "03"],
];

export default function MonitoringLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const tabs = [...TABS, ...(hasRole("admin") ? ADMIN_TABS : [])];

  return (
    <div className="space-y-8">
      {/* Sub-nav — terminal section header */}
      <div className="border-b border-rule-loud">
        <div className="flex items-baseline justify-between gap-4 pb-3">
          <div className="flex items-baseline gap-3">
            <span className="amber-led-static" aria-hidden />
            <p className="label-eyebrow-amber">SECTION // MONITORING</p>
          </div>
          <p className="label-eyebrow hidden sm:block">
            telemetry &middot; ops &middot; soak
          </p>
        </div>
        <nav className="flex flex-wrap items-stretch gap-x-1">
          {tabs.map(([href, label, num]) => {
            // Exact match for /monitoring (Alpha Health) so it doesn't
            // claim to be active when we're on /monitoring/operations.
            // For deeper paths, activate on prefix match.
            const active =
              href === "/monitoring"
                ? pathname === "/monitoring"
                : pathname === href || pathname?.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                className={`group relative flex items-baseline gap-2 px-4 py-2.5 transition-colors ${
                  active ? "text-amber" : "text-paper-dim hover:text-paper"
                }`}
              >
                <span className={`font-mono text-[9px] tracking-[0.2em] ${active ? "text-amber-deep" : "text-paper-low group-hover:text-amber-deep"}`}>
                  {num}
                </span>
                <span className="font-mono text-[12px] font-medium uppercase tracking-[0.14em]">
                  {label}
                </span>
                {active && (
                  <span
                    aria-hidden
                    className="absolute -bottom-[1px] left-2 right-2 h-[2px] bg-amber"
                    style={{ boxShadow: "0 0 12px rgba(251,189,46,0.55)" }}
                  />
                )}
              </Link>
            );
          })}
        </nav>
      </div>

      {children}
    </div>
  );
}
