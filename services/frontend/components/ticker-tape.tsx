"use client";

/**
 * Live ticker tape — scrolls under the header.
 * Pulls /dashboard once a minute and synthesises a tape of symbol · price · pnl%.
 * Falls back to a static educational tape when the API is unreachable so the
 * masthead is never empty.
 */

import { useEffect, useState } from "react";
import { gatewayFetch } from "../lib/api";

interface TapeItem {
  symbol: string;
  price?: number;
  delta?: number;        // signed pct
  meta?: string;         // e.g. "POS", "—"
}

const FALLBACK: TapeItem[] = [
  { symbol: "BTC/USDT", meta: "—" },
  { symbol: "ETH/USDT", meta: "—" },
  { symbol: "BNB/USDT", meta: "—" },
  { symbol: "SOL/USDT", meta: "PARKED" },
  { symbol: "XRP/USDT", meta: "PARKED" },
  { symbol: "DOGE/USDT", meta: "PARKED" },
];

function fmtPct(d?: number) {
  if (d == null || !Number.isFinite(d)) return "·";
  const sign = d >= 0 ? "+" : "";
  return `${sign}${d.toFixed(2)}%`;
}

function fmtPx(p?: number) {
  if (p == null || !Number.isFinite(p)) return "—";
  if (p >= 1000) return p.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (p >= 1)    return p.toFixed(2);
  return p.toFixed(4);
}

export function TickerTape() {
  const [items, setItems] = useState<TapeItem[]>(FALLBACK);

  useEffect(() => {
    let cancelled = false;
    async function pull() {
      try {
        const dash: any = await gatewayFetch("/dashboard");
        const positions = dash?.portfolio?.positions ?? {};
        const stats = dash?.statistics ?? {};
        const next: TapeItem[] = [];

        // Build from known symbols first so order stays consistent
        const symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"];
        for (const s of symbols) {
          const amt = positions[s];
          next.push({
            symbol: s.replace("USDT", "/USDT"),
            meta: amt != null && amt !== 0 ? "POS" : (s === "SOLUSDT" || s === "XRPUSDT" || s === "DOGEUSDT" ? "PARKED" : "—"),
            delta: amt != null ? Number(amt) : undefined,
          });
        }
        // Sharpe / DD / equity tail
        if (stats?.sharpe != null) next.push({ symbol: "SHARPE", meta: stats.sharpe.toFixed(2) });
        if (dash?.portfolio?.unrealized_pnl != null) {
          next.push({ symbol: "uPNL", price: dash.portfolio.unrealized_pnl });
        }
        if (!cancelled && next.length) setItems(next);
      } catch {
        /* keep fallback */
      }
    }
    pull();
    const iv = setInterval(pull, 60_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  // Duplicate so the marquee loops seamlessly
  const loop = [...items, ...items];

  return (
    <div className="relative overflow-hidden border-y border-rule bg-ink-50">
      {/* Edge fade masks */}
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-16 bg-gradient-to-r from-ink-50 to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-16 bg-gradient-to-l from-ink-50 to-transparent" />

      <div className="flex animate-ticker whitespace-nowrap py-2 will-change-transform">
        {loop.map((it, i) => {
          const sign = it.price != null ? (it.price >= 0 ? 1 : -1) : 0;
          return (
            <span key={`${it.symbol}-${i}`} className="flex items-center gap-3 px-6">
              <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-mute">
                {it.symbol}
              </span>
              {it.price != null && (
                <span className={`font-mono text-xs tabular ${sign >= 0 ? "text-mint" : "text-coral"}`}>
                  {sign >= 0 ? "+" : "−"}${Math.abs(it.price).toLocaleString("en-US", { maximumFractionDigits: 0 })}
                </span>
              )}
              {it.delta != null && (
                <span className="font-mono text-xs tabular text-paper-dim">
                  {fmtPx(it.delta)}
                </span>
              )}
              {it.meta && (
                <span className={`font-mono text-[10px] tracking-[0.14em] uppercase ${
                  it.meta === "POS" ? "text-amber"
                  : it.meta === "PARKED" ? "text-paper-low"
                  : "text-paper-mute"
                }`}>
                  {it.meta}
                </span>
              )}
              <span className="text-rule-loud">·</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
