"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import Link from "next/link";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { motion, AnimatePresence } from "framer-motion";

/* ── Types ──────────────────────────────────────────────────── */

interface Recommendation {
  formula_name: string;
  regime: string;
  confidence: number;
}

interface Decision {
  decision_id?: string;
  asset: string;
  action: string;
  signal_score: number;
  timestamp?: string;
}

interface DashboardData {
  portfolio?: {
    total_exposure?: number;
    unrealized_pnl?: number;
    realized_pnl?: number;
    total_pnl?: number;
    positions?: Record<string, number>;
  };
  statistics?: {
    sharpe?: number;
    max_drawdown?: number;
  };
}

/* ── Helpers ────────────────────────────────────────────────── */

function actionDescriptor(a?: string) {
  switch (a) {
    case "BUY":
      return { verb: "Long", noun: "long", color: "text-mint", led: "agent-breath-mint", glow: "rgba(45,212,191,0.18)" };
    case "SELL":
      return { verb: "Short", noun: "short", color: "text-coral", led: "agent-breath-coral", glow: "rgba(248,113,113,0.18)" };
    default:
      return { verb: "Watch", noun: "watching", color: "text-amber", led: "", glow: "rgba(251,189,46,0.18)" };
  }
}

function friendlyAsset(a?: string) {
  if (!a) return "BTC";
  return a.replace("USDT", "");
}

function timeAgo(ts?: string) {
  if (!ts) return "";
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

function fmtUSD(v?: number | null, dp = 0) {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

function fmtSignedUSD(v?: number | null, dp = 2) {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "−";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

/* ── Live UTC clock ─────────────────────────────────────────── */

function useUtcClock() {
  const [stamp, setStamp] = useState<{ date: string; time: string }>({ date: "", time: "" });
  useEffect(() => {
    const tick = () => {
      const d = new Date();
      const w = d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" }).toUpperCase();
      const day = d.toLocaleDateString("en-US", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).toUpperCase();
      const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" });
      setStamp({ date: `${w} · ${day}`, time: `${t} UTC` });
    };
    tick();
    const iv = setInterval(tick, 1_000);
    return () => clearInterval(iv);
  }, []);
  return stamp;
}

/* ── Skeleton ───────────────────────────────────────────────── */

function HeroSkeleton() {
  return (
    <div className="hero-stage min-h-[78vh] flex flex-col justify-center py-20 sm:py-28">
      <div className="space-y-12">
        <div className="skeleton h-3 w-64" />
        <div className="space-y-5">
          <div className="skeleton h-6 w-48" />
          <div className="skeleton h-32 w-[88%]" />
          <div className="skeleton h-4 w-2/3" />
        </div>
        <div className="skeleton h-3 w-80" />
      </div>
    </div>
  );
}

/* ── HERO ───────────────────────────────────────────────────── */

function Hero({
  topRec,
  top,
  equity,
  uPnl,
  rPnl,
  positions,
  halted,
}: {
  topRec?: Recommendation;
  top?: Decision;
  equity: number;
  uPnl: number;
  rPnl: number;
  positions: [string, number][];
  halted: boolean;
}) {
  const { date, time } = useUtcClock();
  const desc = actionDescriptor(top?.action);
  const totalPnl = uPnl + rPnl;
  const pnlPct = equity > 0 ? (totalPnl / equity) * 100 : 0;
  const heldNotional = positions.reduce((acc, [, v]) => acc + Math.abs(Number(v) || 0), 0);
  const conviction = topRec?.confidence ?? 0;

  const status = halted
    ? { label: "AGENT HALTED", led: "bg-coral", color: "text-coral" }
    : { label: "AGENT OPERATING", led: "agent-breath", color: "text-amber" };

  return (
    <section className="hero-stage min-h-[80vh] flex flex-col justify-between py-16 sm:py-20">
      {/* one-shot scan line + cinematic corner brackets (absolute, sit above content) */}
      <span className="hero-scan" aria-hidden />
      <span className="crosshair-frame crosshair-tl" aria-hidden />
      <span className="crosshair-frame crosshair-tr" aria-hidden />
      <span className="crosshair-frame crosshair-bl" aria-hidden />
      <span className="crosshair-frame crosshair-br" aria-hidden />

      {/* ── STATUS BAR ───────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        className="flex flex-wrap items-baseline justify-between gap-4 border-b border-rule-loud pb-4"
      >
        <div className="flex items-baseline gap-3">
          <span
            className={`agent-breath${halted ? " agent-breath-coral" : ""}`}
            aria-hidden
          />
          <p className={`font-mono text-[11px] sm:text-[12px] font-semibold tracking-[0.22em] uppercase ${status.color}`}>
            {status.label}
          </p>
          <span className="hidden md:inline label-eyebrow border-l border-rule-loud pl-3">
            v4.5 · half-kelly · 4-alpha ensemble
          </span>
        </div>
        <p className="font-mono text-[11px] tracking-[0.18em] uppercase text-paper-dim tabular">
          <span className="text-paper-mute">{date}</span>
          <span className="text-paper-low mx-2">·</span>
          <span className="text-amber">{time}</span>
        </p>
      </motion.div>

      {/* ── MAIN HERO ────────────────────────────────────── */}
      <div className="relative flex-1 grid lg:grid-cols-[1fr_auto] items-end gap-12 pt-12 lg:pt-16">
        {/* Left — what the agent IS doing */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.85, ease: [0.22, 1, 0.36, 1], delay: 0.15 }}
          className="space-y-6 min-w-0"
        >
          <p className="label-eyebrow-amber">PRESENT // AUTONOMOUS</p>

          <h1 className="hero-display">
            <span className="text-paper-low">The agent is</span>
            <br />
            <AnimatePresence mode="wait">
              <motion.span
                key={`${desc.verb}-${friendlyAsset(top?.asset)}`}
                initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0)" }}
                exit={{ opacity: 0, y: -10, filter: "blur(6px)" }}
                transition={{ duration: 0.5, ease: "easeOut" }}
                className={`inline-block ${desc.color}`}
                style={{ textShadow: `0 0 38px ${desc.glow}` }}
              >
                {desc.verb} {friendlyAsset(top?.asset)}
              </motion.span>
            </AnimatePresence>
          </h1>

          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.7, delay: 0.55 }}
            className="font-prose text-paper-dim text-base sm:text-lg leading-relaxed max-w-[42ch]"
          >
            {topRec ? (
              <>
                Conviction <span className="text-amber tabular">{(conviction * 100).toFixed(0)}%</span>
                <span className="text-paper-low"> · </span>
                lead alpha <span className="text-paper">{(topRec.formula_name || "ensemble").replace(/_/g, " ")}</span>
                <span className="text-paper-low"> · </span>
                regime <span className="text-paper">{(topRec.regime || "neutral").replace(/_/g, " ")}</span>.
              </>
            ) : (
              <>Half-Kelly sizing engaged. Reading the tape; no headline conviction held.</>
            )}
          </motion.p>
        </motion.div>

        {/* Right — equity card. Discreet but luxuriously typed */}
        <motion.aside
          initial={{ opacity: 0, x: 14 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.7, delay: 0.4 }}
          className="relative shrink-0 w-full lg:w-[340px] border border-rule-loud bg-ink-50/80 backdrop-blur-sm p-7"
        >
          <span className="absolute -top-px left-7 right-7 h-[1px] bg-amber" style={{ boxShadow: "0 0 14px rgba(251,189,46,0.5)" }} />

          <div className="flex items-baseline justify-between mb-5">
            <p className="label-eyebrow-amber">BOOK</p>
            <p className="font-mono text-[10px] tabular text-paper-mute uppercase tracking-[0.18em]">
              held · {fmtUSD(heldNotional)}
            </p>
          </div>

          <p className="font-mono font-medium text-5xl sm:text-[3.4rem] text-paper tabular leading-none tracking-[-0.05em]">
            {fmtUSD(equity)}
          </p>

          <div className="mt-6 space-y-2.5">
            <div className="flex items-baseline justify-between">
              <span className="label-eyebrow">Net P&amp;L</span>
              <span className={`font-mono text-base font-medium tabular ${totalPnl >= 0 ? "text-mint" : "text-coral"}`}>
                {fmtSignedUSD(totalPnl)}
                <span className="text-paper-mute text-[10px] tracking-[0.18em] uppercase ml-2">
                  {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                </span>
              </span>
            </div>
            <div className="flex items-baseline justify-between text-[11px]">
              <span className="label-eyebrow">Realised</span>
              <span className={`font-mono tabular ${rPnl >= 0 ? "text-paper-dim" : "text-coral"}`}>{fmtSignedUSD(rPnl)}</span>
            </div>
            <div className="flex items-baseline justify-between text-[11px]">
              <span className="label-eyebrow">Unrealised</span>
              <span className={`font-mono tabular ${uPnl >= 0 ? "text-paper-dim" : "text-coral"}`}>{fmtSignedUSD(uPnl)}</span>
            </div>
          </div>

          {/* slim positions ledger — just symbols, no numbers */}
          {positions.length > 0 && (
            <div className="mt-6 pt-5 border-t border-rule">
              <p className="label-eyebrow mb-3">Open</p>
              <div className="flex flex-wrap gap-2">
                {positions.slice(0, 6).map(([asset, amt]) => {
                  const v = Number(amt);
                  return (
                    <span
                      key={asset}
                      className={`badge ${v >= 0 ? "badge-profit" : "badge-loss"}`}
                    >
                      {friendlyAsset(asset)} {v >= 0 ? "L" : "S"}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </motion.aside>
      </div>

      {/* ── BOTTOM RAIL — last-action timestamp + nudge ─── */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.85 }}
        className="mt-12 pt-4 border-t border-rule"
      >
        <p className="font-mono text-[11px] tracking-[0.16em] uppercase text-paper-mute">
          {top?.timestamp ? (
            <>
              <span className="text-paper-low">last decision</span>
              <span className="mx-2">·</span>
              <span className="text-paper">{timeAgo(top.timestamp)} ago</span>
              <span className="mx-2">·</span>
              <span className={desc.color}>{desc.verb} {friendlyAsset(top?.asset)}</span>
              <span className="mx-2">·</span>
              <span className="text-paper-mute">score {(top?.signal_score ?? 0).toFixed(2)}</span>
            </>
          ) : (
            <span className="text-paper-low">queue idle · agent reading the tape</span>
          )}
        </p>
      </motion.div>
    </section>
  );
}

/* ── Decision tape — single-line marquee ───────────────────── */

function DecisionTape({ decisions }: { decisions: Decision[] }) {
  // double the items for seamless loop
  const items = useMemo(() => [...decisions, ...decisions], [decisions]);
  if (decisions.length === 0) return null;

  return (
    <motion.section
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.8, delay: 1.05 }}
      className="border-y border-rule overflow-hidden"
    >
      <div className="flex items-center gap-4 py-3">
        <p className="label-eyebrow-amber shrink-0 pl-1">TAPE</p>
        <div className="tape-mask flex-1 overflow-hidden">
          <div className="tape-track whitespace-nowrap">
            {items.map((d, i) => {
              const desc = actionDescriptor(d.action);
              return (
                <span key={`${d.decision_id ?? i}-${i}`} className="inline-flex items-baseline gap-2 px-6 font-mono text-[12px] tracking-[0.05em]">
                  <span className="text-paper-low tabular">{timeAgo(d.timestamp)}</span>
                  <span className={`uppercase font-semibold ${desc.color}`}>{desc.verb}</span>
                  <span className="text-paper">{friendlyAsset(d.asset)}</span>
                  <span className="text-paper-mute tabular">· score {(d.signal_score ?? 0).toFixed(2)}</span>
                  <span className="text-rule-loud px-3">▍</span>
                </span>
              );
            })}
          </div>
        </div>
      </div>
    </motion.section>
  );
}

/* ── Quiet links — "go deeper" ─────────────────────────────── */

const DEEP_LINKS: { href: string; eyebrow: string; title: string; desc: string }[] = [
  {
    href: "/monitoring/operations",
    eyebrow: "Inspect",
    title: "Operations",
    desc: "Order book · signal tape · KPI strip · positions ledger.",
  },
  {
    href: "/chat",
    eyebrow: "Converse",
    title: "Ask the agent",
    desc: "Why long? Why short? Question every decision in plain language.",
  },
  {
    href: "/performance",
    eyebrow: "Audit",
    title: "Track record",
    desc: "Eight-year backtest · walk-forward · deflated Sharpe.",
  },
];

function DeepLinks() {
  return (
    <motion.section
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.7, delay: 1.2 }}
      className="grid sm:grid-cols-3 gap-px bg-rule border border-rule"
    >
      {DEEP_LINKS.map((l, i) => (
        <Link
          key={l.href}
          href={l.href}
          className="group bg-ink-50 px-7 py-8 transition-colors hover:bg-ink-100"
        >
          <p className="label-eyebrow mb-3">
            <span className="font-mono text-[9px] text-paper-low mr-2">0{i + 1}</span>
            {l.eyebrow}
          </p>
          <h3 className="font-mono font-semibold text-2xl text-paper tracking-tight transition-colors group-hover:text-amber">
            {l.title}
            <span className="ml-2 inline-block text-amber transition-transform group-hover:translate-x-1">→</span>
          </h3>
          <p className="mt-3 font-prose text-sm text-paper-dim leading-relaxed">
            {l.desc}
          </p>
        </Link>
      ))}
    </motion.section>
  );
}

/* ── Footer note — philosophy line ─────────────────────────── */

function PhilosophyNote() {
  return (
    <motion.section
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.8, delay: 1.4 }}
      className="pt-2 pb-4"
    >
      <p className="font-prose text-paper-low text-[13px] leading-relaxed max-w-[60ch]">
        <span className="text-amber-deep">▌</span> The terminal stays quiet on purpose. The agent runs autonomously; you are not asked to babysit. Open <span className="text-paper-dim">Operations</span> only when you want the full read of the book.
      </p>
    </motion.section>
  );
}

/* ── Main page component ────────────────────────────────────── */

function DashboardContent() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [halted, setHalted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const loadData = useCallback(() => {
    setLoading(true);
    setError(false);
    Promise.all([
      gatewayFetch("/dashboard").catch(() => null),
      gatewayFetch("/recommendations/BTCUSDT").catch(() => []),
      gatewayFetch("/decisions/history/BTCUSDT").catch(() => []),
    ]).then(([dash, rec, dec]) => {
      if (!dash && (!Array.isArray(rec) || rec.length === 0) && (!Array.isArray(dec) || dec.length === 0)) {
        setError(true);
      }
      setData(dash as DashboardData);
      setRecs(Array.isArray(rec) ? rec : []);
      // Keep most-recent first, but still bounded
      const list = (Array.isArray(dec) ? dec : []).slice(-10).reverse();
      setDecisions(list);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 30_000);
    return () => clearInterval(iv);
  }, [loadData]);

  // Halt-flag probe — best effort; quietly stays false on failure
  useEffect(() => {
    let stopped = false;
    const probe = async () => {
      try {
        const r = await fetch("/api/gateway/risk-service/halt", { cache: "no-store" });
        if (!r.ok) return;
        const j = await r.json();
        if (!stopped) setHalted(Boolean(j?.halted));
      } catch {
        /* keep halted=false */
      }
    };
    probe();
    const iv = setInterval(probe, 30_000);
    return () => {
      stopped = true;
      clearInterval(iv);
    };
  }, []);

  if (loading) return <HeroSkeleton />;

  if (error) {
    return (
      <div className="hero-stage min-h-[60vh] flex flex-col items-center justify-center gap-6 text-center py-24">
        <p className="font-mono font-bold text-2xl text-coral uppercase tracking-[0.08em]">
          ERR_503 // SERVICE OFFLINE
        </p>
        <p className="label-eyebrow">DATA FEED UNREACHABLE</p>
        <button onClick={loadData} className="btn-primary">↻ RETRY</button>
      </div>
    );
  }

  const portfolio = data?.portfolio;
  const topRec = recs[0];
  const top = decisions[0];
  const equity = portfolio?.total_exposure ?? 0;
  const uPnl = portfolio?.unrealized_pnl ?? 0;
  const rPnl = portfolio?.realized_pnl ?? 0;
  const positions = Object.entries(portfolio?.positions ?? {}).filter(
    ([, v]) => v && Number(v) !== 0
  ) as [string, number][];

  return (
    <div className="space-y-12">
      <Hero
        topRec={topRec}
        top={top}
        equity={equity}
        uPnl={uPnl}
        rPnl={rPnl}
        positions={positions}
        halted={halted}
      />
      <DecisionTape decisions={decisions} />
      <DeepLinks />
      <PhilosophyNote />
    </div>
  );
}

export default function DashboardPage() {
  return (
    <AuthGuard>
      <DashboardContent />
    </AuthGuard>
  );
}
