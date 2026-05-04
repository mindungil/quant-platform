"use client";

import { useEffect, useState, useMemo } from "react";
import { motion } from "framer-motion";
import { AuthGuard } from "../../components/auth-guard";

interface Snapshot {
  iter: number;
  ts: string;
  paper: number;
  virtual: number;
  daily_ret_diff_bps: number;
  btc_30d_sr: number;
  eth_6m_sr: number;
  bnb_6m_sr: number;
  max_dd: number;
  warn_symbols: string[];
  n_trades: number;
}

interface LoopState {
  loop_name: string;
  started_at: string;
  target_end: string;
  iteration_count: number;
  baseline_t0: { paper_capital: number; virtual_equity: number; paper_max_dd: number; n_trades: number };
  backtest_expectations: Record<string, { sr: number; cagr_pct: number; max_dd_pct: number }>;
  anomalies_observed: string[];
  next_checkpoint: string;
  stop_conditions: string[];
}

interface SoakResp {
  snapshots: Snapshot[];
  state: LoopState | null;
  error?: string;
}

export default function SoakPage() {
  return (
    <AuthGuard>
      <SoakInner />
    </AuthGuard>
  );
}

function SoakInner() {
  const [data, setData] = useState<SoakResp | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch("/api/soak", { cache: "no-store" });
        const json = (await res.json()) as SoakResp;
        if (!cancelled) setData(json);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const id = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const snapshots = data?.snapshots ?? [];
  const state = data?.state ?? null;
  const latest = snapshots[snapshots.length - 1];

  const elapsedPct = useMemo(() => {
    if (!state) return 0;
    const start = new Date(state.started_at).getTime();
    const end = new Date(state.target_end).getTime();
    const now = Date.now();
    return Math.max(0, Math.min(100, ((now - start) / (end - start)) * 100));
  }, [state]);

  const remainingHours = useMemo(() => {
    if (!state) return 0;
    return Math.max(0, (new Date(state.target_end).getTime() - Date.now()) / 3_600_000);
  }, [state]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="font-mono text-xs text-paper-mute">LOADING_SOAK_DATA…</div>
      </div>
    );
  }

  if (!state || snapshots.length === 0) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-center">
          <p className="label-eyebrow-amber">NO_DATA</p>
          <p className="mt-2 font-prose text-sm text-paper-dim">
            soak loop이 아직 snapshot을 기록하지 않았습니다.
          </p>
        </div>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="space-y-8"
    >
      {/* ─── Masthead ─────────────────────────────────────── */}
      <header>
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <p className="label-eyebrow-amber">▌ V4.5 SOAK MONITOR</p>
            <h1 className="mt-2 font-mono text-3xl font-medium tracking-[-0.02em] text-paper">
              {state.loop_name}
            </h1>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.16em] text-paper-low">
              iter {state.iteration_count} / 120 · {snapshots.length} snapshots logged
            </p>
          </div>
          <div className="text-right">
            <p className="label-eyebrow">REMAINING</p>
            <p className="mt-1 font-mono text-2xl text-amber tabular">
              {remainingHours.toFixed(1)}<span className="ml-1 text-sm text-paper-mute">h</span>
            </p>
          </div>
        </div>

        {/* Progress bar */}
        <div className="mt-5 h-1 w-full bg-rule">
          <motion.div
            className="h-full bg-amber"
            initial={{ width: 0 }}
            animate={{ width: `${elapsedPct}%` }}
            transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
          />
        </div>
        <div className="mt-1.5 flex justify-between font-mono text-[10px] uppercase tracking-[0.14em] text-paper-low">
          <span>{new Date(state.started_at).toISOString().slice(0, 16).replace("T", " ")} START</span>
          <span>{elapsedPct.toFixed(1)}% ELAPSED</span>
          <span>{new Date(state.target_end).toISOString().slice(0, 16).replace("T", " ")} TARGET</span>
        </div>
      </header>

      {/* ─── KPI strip ────────────────────────────────────── */}
      {latest && (
        <div className="grid grid-cols-2 gap-px bg-rule sm:grid-cols-4">
          <KpiCell
            label="PAPER"
            value={`$${latest.paper.toFixed(2)}`}
            delta={latest.paper - state.baseline_t0.paper_capital}
            deltaFmt={(v) => (v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`)}
          />
          <KpiCell
            label="VIRTUAL"
            value={`$${latest.virtual.toFixed(2)}`}
            delta={latest.virtual - state.baseline_t0.virtual_equity}
            deltaFmt={(v) => (v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`)}
          />
          <KpiCell
            label="MAX_DD"
            value={`${(latest.max_dd * 100).toFixed(2)}%`}
            delta={(latest.max_dd - state.baseline_t0.paper_max_dd) * 100}
            deltaFmt={(v) => (v >= 0 ? `+${v.toFixed(2)}pp` : `${v.toFixed(2)}pp`)}
            invertColor
          />
          <KpiCell
            label="DAILY_RET_DIFF"
            value={`${latest.daily_ret_diff_bps >= 0 ? "+" : ""}${latest.daily_ret_diff_bps.toFixed(1)} bps`}
            sub={Math.abs(latest.daily_ret_diff_bps) < 100 ? "WITHIN_GATE" : "WIDE"}
          />
        </div>
      )}

      {/* ─── Equity curves ────────────────────────────────── */}
      <section className="panel-amber-tab bg-ink-50 border border-rule-loud p-6">
        <div className="mb-4 flex items-baseline justify-between">
          <p className="label-eyebrow-amber">▌ PAPER vs VIRTUAL EQUITY</p>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-paper-low">
            {snapshots.length} pts · iter granularity
          </p>
        </div>
        <DualLineChart
          snapshots={snapshots}
          baseline={state.baseline_t0}
          height={260}
        />
        <div className="mt-3 flex gap-5 font-mono text-[11px] uppercase tracking-[0.12em]">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-[2px] w-4 bg-amber" /> PAPER
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-[2px] w-4 bg-cyan" /> VIRTUAL
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-[1px] w-4 border-t border-dashed border-paper-low" /> BASELINE T0
          </span>
        </div>
      </section>

      {/* ─── Per-symbol SR trend grid ─────────────────────── */}
      <section>
        <p className="mb-4 label-eyebrow-amber">▌ ALPHA SHARPE TRENDS</p>
        <div className="grid gap-px bg-rule sm:grid-cols-3">
          <SymbolTrend
            label="BTC // 30D ROLLING"
            data={snapshots.map((s) => s.btc_30d_sr)}
            current={latest?.btc_30d_sr ?? 0}
            expected={state.backtest_expectations.btc.sr}
            warnThreshold={0}
          />
          <SymbolTrend
            label="ETH // 6M ROLLING"
            data={snapshots.map((s) => s.eth_6m_sr)}
            current={latest?.eth_6m_sr ?? 0}
            expected={state.backtest_expectations.eth.sr}
            warnThreshold={0}
          />
          <SymbolTrend
            label="BNB // 6M ROLLING"
            data={snapshots.map((s) => s.bnb_6m_sr)}
            current={latest?.bnb_6m_sr ?? 0}
            expected={state.backtest_expectations.bnb.sr}
            warnThreshold={0}
          />
        </div>
      </section>

      {/* ─── Snapshot ledger ──────────────────────────────── */}
      <section>
        <div className="mb-3 flex items-baseline justify-between">
          <p className="label-eyebrow-amber">▌ SNAPSHOT LEDGER</p>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-paper-low">
            most recent first
          </p>
        </div>
        <div className="border border-rule-loud bg-ink-50">
          <div className="grid grid-cols-[60px_1fr_110px_110px_90px_90px_120px] gap-3 px-4 py-2.5 border-b border-rule font-mono text-[10px] uppercase tracking-[0.14em] text-paper-low">
            <span>ITER</span>
            <span>TIMESTAMP</span>
            <span className="text-right">PAPER</span>
            <span className="text-right">VIRTUAL</span>
            <span className="text-right">MAX_DD</span>
            <span className="text-right">RET Δbps</span>
            <span>WARN</span>
          </div>
          <div className="max-h-[480px] overflow-y-auto">
            {[...snapshots].reverse().map((s, i) => (
              <div
                key={s.iter}
                className={`grid grid-cols-[60px_1fr_110px_110px_90px_90px_120px] gap-3 px-4 py-2.5 font-mono text-[12px] tabular ${
                  i % 2 === 0 ? "bg-ink-100/40" : ""
                } hover:bg-amber/5 transition-colors`}
              >
                <span className="text-paper-low">#{s.iter}</span>
                <span className="text-paper-dim">
                  {new Date(s.ts).toISOString().slice(0, 19).replace("T", " ")}
                </span>
                <span className="text-right text-paper">${s.paper.toFixed(2)}</span>
                <span className="text-right text-paper">${s.virtual.toFixed(2)}</span>
                <span
                  className={`text-right ${
                    s.max_dd > 0.2 ? "text-coral" : s.max_dd > 0.15 ? "text-amber" : "text-paper-dim"
                  }`}
                >
                  {(s.max_dd * 100).toFixed(2)}%
                </span>
                <span
                  className={`text-right ${
                    Math.abs(s.daily_ret_diff_bps) > 100 ? "text-coral" : "text-paper-dim"
                  }`}
                >
                  {s.daily_ret_diff_bps >= 0 ? "+" : ""}
                  {s.daily_ret_diff_bps.toFixed(1)}
                </span>
                <span className="text-amber text-[10px] uppercase tracking-[0.12em]">
                  {s.warn_symbols.length > 0 ? s.warn_symbols.map((w) => w.replace("USDT", "")).join(",") : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── Anomalies + stop conditions ──────────────────── */}
      <section className="grid gap-px bg-rule sm:grid-cols-2">
        <div className="bg-ink-50 p-5">
          <p className="label-eyebrow-amber mb-3">▌ ANOMALIES OBSERVED</p>
          {state.anomalies_observed.length === 0 ? (
            <p className="font-prose text-sm text-paper-dim">none recorded</p>
          ) : (
            <ul className="space-y-2">
              {state.anomalies_observed.map((a, i) => (
                <li key={i} className="flex gap-2 font-mono text-[11px] text-paper-dim">
                  <span className="text-amber shrink-0">▸</span>
                  <span>{a}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="bg-ink-50 p-5">
          <p className="label-eyebrow-amber mb-3">▌ STOP CONDITIONS</p>
          <ul className="space-y-2">
            {state.stop_conditions.map((c, i) => (
              <li key={i} className="flex gap-2 font-mono text-[11px] text-paper-dim">
                <span className="text-coral shrink-0">⊘</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <p className="text-center font-mono text-[10px] uppercase tracking-[0.16em] text-paper-low">
        {state.next_checkpoint}
      </p>
    </motion.div>
  );
}

/* ── Components ─────────────────────────────────────────────── */

function KpiCell({
  label,
  value,
  delta,
  deltaFmt,
  sub,
  invertColor,
}: {
  label: string;
  value: string;
  delta?: number;
  deltaFmt?: (v: number) => string;
  sub?: string;
  invertColor?: boolean;
}) {
  const isPositive = (delta ?? 0) >= 0;
  const goodColor = invertColor ? isPositive ? "text-coral" : "text-mint" : isPositive ? "text-mint" : "text-coral";
  return (
    <div className="bg-ink-50 p-4 sm:p-5">
      <p className="label-eyebrow text-paper-low">{label}</p>
      <p className="mt-1.5 font-mono text-2xl font-medium text-paper tabular">{value}</p>
      {delta !== undefined && deltaFmt && (
        <p className={`mt-1 font-mono text-[11px] tabular ${goodColor}`}>{deltaFmt(delta)}</p>
      )}
      {sub && (
        <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-paper-low">{sub}</p>
      )}
    </div>
  );
}

function SymbolTrend({
  label,
  data,
  current,
  expected,
  warnThreshold,
}: {
  label: string;
  data: number[];
  current: number;
  expected: number;
  warnThreshold: number;
}) {
  const above = current > warnThreshold;
  return (
    <div className="bg-ink-50 p-5">
      <div className="flex items-baseline justify-between mb-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-paper-low">{label}</p>
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.12em] ${
            above ? "text-mint" : "text-amber"
          }`}
        >
          {above ? "● ACTIVE" : "● WARN"}
        </span>
      </div>
      <p className={`font-mono text-3xl font-medium tabular ${above ? "text-mint" : "text-amber"}`}>
        {current >= 0 ? "+" : ""}
        {current.toFixed(2)}
      </p>
      <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.12em] text-paper-low">
        EXPECTED {expected.toFixed(2)} · Δ {(current - expected).toFixed(2)}
      </p>
      <div className="mt-4">
        <LineChart data={data} height={56} color={above ? "#2dd4bf" : "#fbbd2e"} zeroLine />
      </div>
    </div>
  );
}

function DualLineChart({
  snapshots,
  baseline,
  height,
}: {
  snapshots: Snapshot[];
  baseline: { paper_capital: number; virtual_equity: number };
  height: number;
}) {
  const width = 1000;
  const pad = { top: 12, right: 12, bottom: 24, left: 60 };

  const allValues = snapshots.flatMap((s) => [s.paper, s.virtual]);
  allValues.push(baseline.paper_capital, baseline.virtual_equity);
  const min = Math.min(...allValues) * 0.998;
  const max = Math.max(...allValues) * 1.002;
  const range = max - min || 1;

  const xScale = (i: number) =>
    pad.left + (i / Math.max(snapshots.length - 1, 1)) * (width - pad.left - pad.right);
  const yScale = (v: number) =>
    pad.top + (1 - (v - min) / range) * (height - pad.top - pad.bottom);

  const paperPath = snapshots
    .map((s, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)},${yScale(s.paper).toFixed(1)}`)
    .join(" ");
  const virtualPath = snapshots
    .map((s, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)},${yScale(s.virtual).toFixed(1)}`)
    .join(" ");

  // Y-axis ticks
  const ticks = [min, (min + max) / 2, max];

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" className="w-full">
      {/* Grid */}
      {ticks.map((t, i) => (
        <g key={i}>
          <line
            x1={pad.left}
            x2={width - pad.right}
            y1={yScale(t)}
            y2={yScale(t)}
            stroke="rgba(170,178,189,0.08)"
            strokeWidth={1}
          />
          <text
            x={pad.left - 8}
            y={yScale(t) + 3}
            textAnchor="end"
            fontSize={9}
            fontFamily="monospace"
            fill="rgba(170,178,189,0.6)"
          >
            ${t.toFixed(0)}
          </text>
        </g>
      ))}
      {/* Baselines (dashed) */}
      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={yScale(baseline.paper_capital)}
        y2={yScale(baseline.paper_capital)}
        stroke="#fbbd2e"
        strokeWidth={1}
        strokeDasharray="3,3"
        opacity={0.35}
      />
      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={yScale(baseline.virtual_equity)}
        y2={yScale(baseline.virtual_equity)}
        stroke="#58a6ff"
        strokeWidth={1}
        strokeDasharray="3,3"
        opacity={0.35}
      />
      {/* Paper line */}
      <motion.path
        d={paperPath}
        fill="none"
        stroke="#fbbd2e"
        strokeWidth={1.8}
        strokeLinejoin="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 1.0, ease: "easeOut" }}
      />
      {/* Virtual line */}
      <motion.path
        d={virtualPath}
        fill="none"
        stroke="#58a6ff"
        strokeWidth={1.8}
        strokeLinejoin="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 1.0, ease: "easeOut", delay: 0.15 }}
      />
      {/* End markers */}
      {snapshots.length > 0 && (
        <>
          <circle
            cx={xScale(snapshots.length - 1)}
            cy={yScale(snapshots[snapshots.length - 1].paper)}
            r={3}
            fill="#fbbd2e"
          />
          <circle
            cx={xScale(snapshots.length - 1)}
            cy={yScale(snapshots[snapshots.length - 1].virtual)}
            r={3}
            fill="#58a6ff"
          />
        </>
      )}
      {/* X-axis labels */}
      <text
        x={pad.left}
        y={height - 6}
        fontSize={9}
        fontFamily="monospace"
        fill="rgba(170,178,189,0.6)"
      >
        iter #{snapshots[0]?.iter ?? "–"}
      </text>
      <text
        x={width - pad.right}
        y={height - 6}
        textAnchor="end"
        fontSize={9}
        fontFamily="monospace"
        fill="rgba(170,178,189,0.6)"
      >
        iter #{snapshots[snapshots.length - 1]?.iter ?? "–"}
      </text>
    </svg>
  );
}

function LineChart({
  data,
  height,
  color,
  zeroLine,
}: {
  data: number[];
  height: number;
  color: string;
  zeroLine?: boolean;
}) {
  if (data.length < 2) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-[10px] text-paper-low">
        AWAITING DATA
      </div>
    );
  }
  const width = 200;
  const min = Math.min(...data, zeroLine ? 0 : Infinity);
  const max = Math.max(...data, zeroLine ? 0 : -Infinity);
  const range = max - min || 1;
  const yScale = (v: number) => 4 + (1 - (v - min) / range) * (height - 8);
  const xScale = (i: number) => (i / (data.length - 1)) * width;

  const path = data
    .map((v, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`)
    .join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      {zeroLine && min < 0 && max > 0 && (
        <line
          x1={0}
          x2={width}
          y1={yScale(0)}
          y2={yScale(0)}
          stroke="rgba(170,178,189,0.25)"
          strokeWidth={0.5}
          strokeDasharray="2,2"
        />
      )}
      <motion.path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.8, ease: "easeOut" }}
      />
      <circle cx={xScale(data.length - 1)} cy={yScale(data[data.length - 1])} r={2} fill={color} />
    </svg>
  );
}
