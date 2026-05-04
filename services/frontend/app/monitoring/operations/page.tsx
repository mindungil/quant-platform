"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../../lib/api";
import { AuthGuard } from "../../../components/auth-guard";
import { useToast } from "../../../components/toast";
import {
  parseReasoning,
  cleanReasoning,
  formatStrategyDescription,
  formatRegime,
  beginnerAction,
  beginnerIndicatorName,
} from "../../../lib/reasoning";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  motion,
} from "../../../components/motion";
import { MarketChart } from "../../../components/market-chart";
import { IconEmpty } from "../../../components/icons";

/* ── Types ──────────────────────────────────────────────────── */

interface Recommendation {
  name: string;
  description: string;
  formula_name: string;
  regime: string;
  confidence: number;
  reasoning: string;
}

interface Decision {
  decision_id?: string;
  asset: string;
  action: string;
  signal_score: number;
  reasoning?: string;
  timestamp?: string;
  threshold_crossed?: boolean;
  components?: Record<string, number>;
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
    trade_count?: number;
    total_return?: number;
    sharpe?: number;
    win_rate?: number;
    profit_factor?: number;
    max_drawdown?: number;
  };
  active_strategy?: { name?: string; status?: string } | null;
  orders?: Array<{ asset?: string; side?: string; status?: string }>;
}

/* ── Helpers ────────────────────────────────────────────────── */

function actionWord(a?: string) {
  switch (a) {
    case "BUY":  return { word: "Long",  cls: "text-mint",  led: "led-mint"  };
    case "SELL": return { word: "Short", cls: "text-coral", led: "led-coral" };
    default:     return { word: "Hold",  cls: "text-amber", led: ""           };
  }
}

function friendlyAsset(a: string) {
  return a?.replace("USDT", "") ?? a;
}

function timeAgo(ts: string) {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function fmtUSD(v?: number | null, dp = 0) {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}

/* ── Decision row — ledger style ────────────────────────────── */

function DecisionRow({ decision }: { decision: Decision }) {
  const { structured } = parseReasoning(decision.reasoning || "");
  const w = actionWord(decision.action);
  const tStr = decision.timestamp ? timeAgo(decision.timestamp) : "";
  const bull = structured?.bullish_indicators || [];
  const bear = structured?.bearish_indicators || [];
  const regime = structured?.regime || "";

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.4 }}
      className="border-b border-rule py-4 last:border-b-0"
    >
      <div className="flex items-baseline justify-between gap-4">
        <div className="flex items-baseline gap-3 min-w-0 flex-wrap">
          <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-low tabular">
            {tStr}
          </span>
          <span className="font-mono font-medium text-base text-paper uppercase tracking-tight">
            {friendlyAsset(decision.asset)}
          </span>
          <span className={`font-mono font-bold text-base uppercase tracking-[0.08em] ${w.cls}`}>
            {w.word.toUpperCase()}
          </span>
        </div>
        <span className="font-mono text-[11px] tabular text-paper-mute whitespace-nowrap uppercase tracking-[0.08em]">
          score={(decision.signal_score ?? 0).toFixed(2)}
        </span>
      </div>
      {regime && (
        <p className="mt-2 font-prose text-sm text-paper-dim">
          {formatRegime(regime)}
        </p>
      )}
      {(bull.length > 0 || bear.length > 0) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {bull.slice(0, 4).map((i: any) => (
            <span key={i.name} className="badge badge-profit">
              {beginnerIndicatorName(i.name)}
            </span>
          ))}
          {bear.slice(0, 2).map((i: any) => (
            <span key={i.name} className="badge badge-loss">
              {beginnerIndicatorName(i.name)}
            </span>
          ))}
        </div>
      )}
    </motion.div>
  );
}

/* ── Confidence bar — amber ─────────────────────────────────── */

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <p className="label-eyebrow">Confidence</p>
        <span className="font-mono text-xs tabular text-amber">{pct}%</span>
      </div>
      <div className="h-[3px] bg-rule">
        <motion.div
          className="h-full bg-amber"
          style={{ boxShadow: "0 0 10px rgba(251,189,46,0.55)" }}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1.1, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
    </div>
  );
}

/* ── Skeleton ───────────────────────────────────────────────── */

function OperationsSkeleton() {
  return (
    <div className="space-y-12">
      <div className="space-y-4">
        <div className="skeleton h-3 w-32" />
        <div className="skeleton h-16 w-2/3" />
        <div className="skeleton h-3 w-48" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-rule">
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-ink p-6 space-y-3">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton h-10 w-32" />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Main Operations View ───────────────────────────────────── */

function OperationsContent() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [kimchi, setKimchi] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [now, setNow] = useState<string>("");
  const toast = useToast();

  useEffect(() => {
    const tick = () => {
      const d = new Date();
      const fmt = d.toLocaleString("en-US", {
        weekday: "long", month: "long", day: "numeric", year: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: false,
      });
      setNow(fmt + " UTC" + (d.getTimezoneOffset() / -60).toString().padStart(2, "+0"));
    };
    tick();
    const iv = setInterval(tick, 30_000);
    return () => clearInterval(iv);
  }, []);

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
        toast.show("error", "운영 데이터를 불러오지 못했습니다");
      }
      setData(dash as DashboardData);
      setRecs(Array.isArray(rec) ? rec : []);
      setDecisions((Array.isArray(dec) ? dec : []).slice(-5).reverse());
      setLoading(false);
    });
  }, [toast]);

  useEffect(() => { loadData(); }, [loadData]);

  useEffect(() => {
    const load = () => {
      fetch("/api/gateway/external/kimchi-premium/BTC")
        .then((r) => r.json()).then(setKimchi).catch(() => {});
    };
    load();
    const iv = setInterval(load, 30_000);
    return () => clearInterval(iv);
  }, []);

  if (loading) return <OperationsSkeleton />;

  if (error) {
    return (
      <PageTransition>
        <div className="flex flex-col items-center gap-6 py-24 text-center">
          <p className="font-mono font-bold text-2xl text-coral uppercase tracking-[0.08em]">
            ERR_503 // SERVICE OFFLINE
          </p>
          <p className="label-eyebrow">DATA FEED UNREACHABLE</p>
          <button onClick={() => { setError(false); loadData(); }} className="btn-primary">
            ↻ RETRY
          </button>
        </div>
      </PageTransition>
    );
  }

  const portfolio = data?.portfolio;
  const stats = data?.statistics;
  const topRec = recs[0];
  const top = decisions[0];
  const w = actionWord(top?.action);
  const equity = portfolio?.total_exposure ?? 0;
  const uPnl = portfolio?.unrealized_pnl ?? 0;
  const rPnl = portfolio?.realized_pnl ?? 0;
  const positions = portfolio?.positions ?? {};
  const positionEntries = Object.entries(positions).filter(([, v]) => v && Number(v) !== 0);

  return (
    <PageTransition>
      <div className="space-y-16">

        {/* ═══════ MASTHEAD — terminal banner ═══════════════════ */}
        <FadeInView>
          <header className="space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule-loud pb-3">
              <div className="flex items-baseline gap-3">
                <span className="amber-led" aria-hidden />
                <p className="label-eyebrow-amber">SESSION // OPERATIONS.MAIN</p>
                <span className="hidden md:inline label-eyebrow border-l border-rule-loud pl-3">v4.5 // half_kelly=0.5</span>
              </div>
              <p className="label-eyebrow tabular">{now}</p>
            </div>

            <div className="grid gap-6 lg:grid-cols-[1fr_auto] items-end pt-2">
              <div className="space-y-4 min-w-0">
                <h1 className="font-mono font-bold text-2xl sm:text-3xl leading-[1.15] uppercase tracking-[-0.02em] text-paper">
                  <span className="text-amber">&gt;</span> SIGNAL
                  <span className="text-paper-low"> // </span>
                  <span className={w.cls}>{w.word.toUpperCase()}</span>
                  <span className="text-paper-low"> // BTC</span>
                </h1>
                {topRec && (
                  <p className="font-prose text-[15px] leading-relaxed max-w-2xl text-paper-dim">
                    {formatRegime(topRec.regime)} regime detected. Half-Kelly sizing engaged on active alphas — <span className="text-amber font-medium">{topRec.formula_name?.replace(/_/g, " ") || "ensemble"}</span> currently leads conviction.
                  </p>
                )}
              </div>

              <div className="shrink-0 border-t lg:border-t-0 lg:border-l border-rule-loud pt-4 lg:pt-0 lg:pl-8">
                <p className="label-eyebrow mb-2">EQUITY · LIVE</p>
                <p className="font-mono font-medium text-5xl sm:text-6xl text-paper tabular leading-none tracking-[-0.04em]">
                  {fmtUSD(equity)}
                </p>
                <div className="mt-4 flex flex-wrap items-baseline gap-x-5 gap-y-1.5 font-mono text-xs">
                  <span className={`tabular ${uPnl >= 0 ? "text-mint" : "text-coral"}`}>
                    {uPnl >= 0 ? "▲" : "▼"} {fmtUSD(uPnl)} <span className="text-paper-mute uppercase tracking-[0.12em] text-[10px] ml-1">unrl</span>
                  </span>
                  <span className={`tabular ${rPnl >= 0 ? "text-mint" : "text-coral"}`}>
                    {rPnl >= 0 ? "▲" : "▼"} {fmtUSD(rPnl)} <span className="text-paper-mute uppercase tracking-[0.12em] text-[10px] ml-1">rlzd</span>
                  </span>
                </div>
              </div>
            </div>
          </header>
        </FadeInView>

        {/* ═══════ KPI STRIP ══════════════════════════════════ */}
        <FadeInView delay={0.05}>
          <section>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-rule border border-rule">
              {[
                { k: "Sharpe (live)", v: stats?.sharpe != null ? stats.sharpe.toFixed(2) : "—", sub: "annualised" },
                { k: "Trades",        v: stats?.trade_count ?? "—",                              sub: "since reset" },
                { k: "Win rate",      v: stats?.win_rate != null ? `${(stats.win_rate * 100).toFixed(0)}%` : "—", sub: "rolling 30d" },
                { k: "Max DD",        v: stats?.max_drawdown != null ? `${(stats.max_drawdown * 100).toFixed(1)}%` : "—", sub: "v4.5 era" },
              ].map((kpi, i) => (
                <div key={kpi.k} className="bg-ink-50 px-5 py-6 panel-amber-tab">
                  <p className="label-eyebrow mb-3">
                    <span className="font-mono text-[9px] text-paper-low mr-2">0{i+1}</span>
                    {kpi.k}
                  </p>
                  <p className="font-mono font-medium text-4xl text-paper tabular leading-none">
                    {kpi.v}
                  </p>
                  <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.14em] text-paper-low">
                    {kpi.sub}
                  </p>
                </div>
              ))}
            </div>
          </section>
        </FadeInView>

        {/* ═══════ HERO ROW — Action card + Confidence ═══════ */}
        <FadeInView delay={0.1}>
          <section className="grid lg:grid-cols-[1fr_360px] gap-px bg-rule border border-rule">
            <div className="bg-ink-50 px-8 py-10 relative">
              <span className="absolute top-0 left-8 h-[2px] w-20 bg-amber" style={{ boxShadow: "0 0 14px rgba(251,189,46,0.55)" }} />
              <div className="flex items-baseline gap-3">
                <span className={`amber-led-static ${w.led}`} aria-hidden />
                <p className="label-eyebrow-amber">SIGNAL.CURRENT // BTCUSDT</p>
              </div>
              <div className="mt-6 flex flex-wrap items-baseline gap-x-6 gap-y-2">
                <h2 className={`font-mono font-bold text-6xl sm:text-7xl leading-none uppercase tracking-[-0.04em] ${w.cls}`}>
                  {w.word.toUpperCase()}
                </h2>
                <p className="font-prose text-base text-paper-mute uppercase tracking-[0.18em]">
                  {beginnerAction(top?.action).text}
                </p>
              </div>
              {topRec && (
                <p className="mt-6 font-prose text-paper-dim text-[15px] leading-relaxed max-w-2xl">
                  {formatStrategyDescription(cleanReasoning(topRec.reasoning))}
                </p>
              )}
            </div>

            <div className="bg-ink-50 px-7 py-10 space-y-7">
              {topRec ? (
                <>
                  <ConfidenceBar value={topRec.confidence} />
                  <div className="space-y-3">
                    <p className="label-eyebrow">Strategy</p>
                    <p className="font-mono font-medium text-2xl text-paper">
                      {topRec.formula_name?.replace(/_/g, " ") || "Ensemble"}
                    </p>
                  </div>
                  <div className="space-y-3">
                    <p className="label-eyebrow">Regime</p>
                    <p className="text-paper text-base">{formatRegime(topRec.regime)}</p>
                  </div>
                </>
              ) : (
                <div className="flex flex-col items-center justify-center h-full py-12">
                  <motion.div
                    className="w-10 h-10 rounded-full border border-rule-loud border-t-amber"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1.4, repeat: Infinity, ease: "linear" }}
                  />
                  <p className="mt-4 label-eyebrow">analysing market</p>
                </div>
              )}
            </div>
          </section>
        </FadeInView>

        {/* ═══════ CHART + KIMCHI ═════════════════════════════ */}
        <FadeInView delay={0.15}>
          <section className="grid lg:grid-cols-[1fr_320px] gap-px bg-rule border border-rule">
            <div className="bg-ink-50">
              <MarketChart asset="BTCUSDT" />
            </div>
            <div className="bg-ink-50 px-6 py-6 flex flex-col">
              <div className="flex items-center gap-2 mb-5">
                <span className="amber-led" aria-hidden />
                <p className="label-eyebrow-amber">KIMCHI.SPREAD</p>
              </div>
              {kimchi && typeof kimchi.premium_pct === "number" && !kimchi.error ? (
                <>
                  <p className={`font-mono font-medium text-5xl tabular leading-none tracking-[-0.04em] ${
                    kimchi.premium_pct >= 2 ? "text-coral"
                    : kimchi.premium_pct <= -1 ? "text-mint"
                    : "text-paper"
                  }`}>
                    {kimchi.premium_pct >= 0 ? "+" : ""}{Number(kimchi.premium_pct).toFixed(2)}%
                  </p>
                  <div className="mt-5 ledger-row">
                    <span className="label-eyebrow">KRW · UPBIT</span>
                    <span className="font-mono text-sm tabular text-paper">
                      {kimchi.krw_price ? `₩${Number(kimchi.krw_price).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}` : "—"}
                    </span>
                  </div>
                  <div className="ledger-row">
                    <span className="label-eyebrow">USDT · BINANCE</span>
                    <span className="font-mono text-sm tabular text-paper">
                      {kimchi.usdt_price ? `$${Number(kimchi.usdt_price).toLocaleString("en-US", { maximumFractionDigits: 0 })}` : "—"}
                    </span>
                  </div>
                  <p className="mt-5 font-prose text-paper-dim text-sm leading-relaxed">
                    {kimchi.premium_pct > 2
                      ? "Premium frothy — short-Kimchi tape signal active."
                      : kimchi.premium_pct < -1
                      ? "Discount window open — accumulation favoured."
                      : "Spread within steady-state band."}
                  </p>
                </>
              ) : (
                <div className="flex-1 flex items-center justify-center">
                  <p className="label-eyebrow">loading…</p>
                </div>
              )}
            </div>
          </section>
        </FadeInView>

        {/* ═══════ POSITIONS LEDGER + DECISIONS ═══════════════ */}
        <section className="grid lg:grid-cols-[460px_1fr] gap-px bg-rule border border-rule">
          <FadeInView className="bg-ink-50">
            <div className="px-6 py-7">
              <div className="flex items-baseline justify-between mb-5">
                <h3 className="font-mono font-bold text-base uppercase tracking-[0.18em] text-paper">
                  <span className="text-amber">▌</span> POSITIONS
                </h3>
                <p className="label-eyebrow">N={positionEntries.length}</p>
              </div>
              {positionEntries.length === 0 ? (
                <div className="flex flex-col items-center py-12 text-center">
                  <IconEmpty />
                  <p className="mt-4 label-eyebrow">FLAT // NO POSITIONS HELD</p>
                </div>
              ) : (
                <div>
                  <div className="grid grid-cols-[1fr_auto_auto] gap-4 pb-2 border-b border-rule-loud">
                    <span className="label-eyebrow">SYMBOL</span>
                    <span className="label-eyebrow text-right">NOTIONAL</span>
                    <span className="label-eyebrow text-right">SIDE</span>
                  </div>
                  {positionEntries.map(([asset, amt]) => {
                    const v = Number(amt);
                    return (
                      <div key={asset} className="grid grid-cols-[1fr_auto_auto] gap-4 items-baseline py-3 border-b border-rule">
                        <div className="flex items-baseline gap-3">
                          <span className="font-mono text-[10px] text-paper-low tabular">
                            {asset.slice(0, 3)}
                          </span>
                          <span className="font-mono font-medium text-lg text-paper uppercase tracking-tight">
                            {friendlyAsset(asset)}
                          </span>
                        </div>
                        <span className={`font-mono text-sm tabular text-right ${v >= 0 ? "text-paper" : "text-coral"}`}>
                          {fmtUSD(Math.abs(v))}
                        </span>
                        <span className={`badge ${v >= 0 ? "badge-profit" : "badge-loss"}`}>
                          {v >= 0 ? "LONG" : "SHORT"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </FadeInView>

          <FadeInView delay={0.05} className="bg-ink-50">
            <div className="px-7 py-7">
              <div className="flex items-baseline justify-between mb-5">
                <h3 className="font-mono font-bold text-base uppercase tracking-[0.18em] text-paper">
                  <span className="text-amber">▌</span> SIGNAL TAPE
                </h3>
                <p className="label-eyebrow">N={decisions.length} · last hour</p>
              </div>
              {decisions.length === 0 ? (
                <div className="flex flex-col items-center py-16 text-center">
                  <IconEmpty />
                  <p className="mt-4 label-eyebrow">QUEUE EMPTY // NO SIGNALS</p>
                </div>
              ) : (
                <StaggerContainer>
                  {decisions.map((d, i) => (
                    <StaggerItem key={d.decision_id ?? i}>
                      <DecisionRow decision={d} />
                    </StaggerItem>
                  ))}
                </StaggerContainer>
              )}
            </div>
          </FadeInView>
        </section>

        {/* ═══════ SYSTEM INFO ════════════════════════════════ */}
        <FadeInView delay={0.1}>
          <footer className="border-t border-rule-loud pt-6 grid grid-cols-1 sm:grid-cols-3 gap-6">
            <div>
              <p className="label-eyebrow-amber mb-2">SIZING_MODE</p>
              <p className="font-prose text-paper-dim text-sm leading-relaxed">
                Half-Kelly · EMA-smoothed live guards · walk-forward purged validation.
              </p>
            </div>
            <div>
              <p className="label-eyebrow-cyan mb-2">EXECUTION</p>
              <p className="font-prose text-paper-dim text-sm leading-relaxed">
                Paper book on Binance + Upbit · virtual sim runs in lockstep for daily-return alignment.
              </p>
            </div>
            <div>
              <p className="label-eyebrow mb-2">BUILD</p>
              <p className="font-prose text-paper-dim text-sm leading-relaxed">
                v4.5 · kelly=0.5 · 4-alpha ensemble (momentum / range / vol / carry)
              </p>
            </div>
          </footer>
        </FadeInView>
      </div>
    </PageTransition>
  );
}

export default function OperationsPage() {
  return (
    <AuthGuard>
      <OperationsContent />
    </AuthGuard>
  );
}
