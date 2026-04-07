"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { useToast } from "../../components/toast";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  AnimatedNumber,
  motion,
} from "../../components/motion";
import { MarketChart } from "../../components/market-chart";

/* ── Types ───────────────────────────────────────────────────── */

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
    concentration?: Record<string, number>;
  };
  statistics?: {
    trade_count?: number;
    total_return?: number;
    sharpe?: number;
    win_rate?: number;
    profit_factor?: number;
  };
  active_strategy?: {
    name?: string;
    status?: string;
  } | null;
  orders?: Array<{ asset?: string; side?: string; status?: string }>;
}

/* ── Helpers ─────────────────────────────────────────────────── */

function friendlyAction(action: string): { label: string; color: string; bg: string } {
  switch (action) {
    case "BUY":
      return { label: "매수", color: "text-emerald-400", bg: "bg-emerald-500/15 border-emerald-500/30" };
    case "SELL":
      return { label: "매도", color: "text-red-400", bg: "bg-red-500/15 border-red-500/30" };
    default:
      return { label: "관망", color: "text-neutral-400", bg: "bg-white/[0.02] border-white/[0.06]" };
  }
}

function friendlyRegime(regime: string): string {
  const lower = regime?.toLowerCase() ?? "";
  if (lower.includes("bull") || lower.includes("up")) return "상승 추세";
  if (lower.includes("bear") || lower.includes("down")) return "하락 추세";
  if (lower.includes("volatile") || lower.includes("vol")) return "변동성 확대";
  if (lower.includes("range") || lower.includes("sideways") || lower.includes("neutral")) return "횡보";
  return regime || "분석 중";
}

function friendlyFormula(name: string): string {
  const lower = name?.toLowerCase() ?? "";
  if (lower.includes("momentum")) return "모멘텀 분석";
  if (lower.includes("mean_revert") || lower.includes("reversion")) return "평균 회귀 분석";
  if (lower.includes("trend")) return "추세 추종 분석";
  if (lower.includes("breakout")) return "돌파 분석";
  if (lower.includes("volatility")) return "변동성 분석";
  return name || "복합 분석";
}

function friendlyAsset(asset: string): string {
  return asset?.replace("USDT", "") ?? asset;
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "방금 전";
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  return `${days}일 전`;
}

function formatMoney(v: number | undefined | null): string {
  if (v == null) return "--";
  return v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
}

/* ── Reasoning Card ──────────────────────────────────────────── */

function ReasoningCard({ reasoning }: { reasoning: string }) {
  // Try to parse structured reasoning
  let data: any = null;
  try {
    const parsed = JSON.parse(reasoning);
    if (parsed.structured) data = parsed.structured;
  } catch {
    // fallback: plain text
  }

  if (!data) {
    // Strip [formula=...] prefix for plain text display
    const cleanText = reasoning.replace(/^\[.*?\]\s*/, '');
    return <p className="text-sm text-zinc-400 leading-relaxed">{cleanText}</p>;
  }

  return (
    <div className="space-y-3">
      {/* Summary with emphasis */}
      <p className="text-sm font-semibold text-white">
        {data.summary}
      </p>

      {/* Score + Regime badges */}
      <div className="flex flex-wrap gap-2">
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
          data.action === 'BUY' ? 'bg-emerald-500/15 text-emerald-400' :
          data.action === 'SELL' ? 'bg-red-500/15 text-red-400' :
          'bg-white/[0.08] text-zinc-400'
        }`}>
          {data.direction} &middot; {data.strength}
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-0.5 text-xs text-zinc-500">
          {data.regime}
        </span>
        {data.formula && (
          <span className="inline-flex items-center rounded-full bg-white/[0.06] px-2.5 py-0.5 text-xs text-zinc-500">
            {data.formula}
          </span>
        )}
      </div>

      {/* Signal strength bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-[10px] text-zinc-500">
          <span>시그널 강도</span>
          <span>{(data.abs_score * 100).toFixed(0)}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-white/[0.06]">
          <div
            className={`h-full rounded-full transition-all ${
              data.score >= 0 ? 'bg-emerald-500' : 'bg-red-500'
            }`}
            style={{ width: `${Math.min(data.abs_score * 100, 100)}%` }}
          />
        </div>
      </div>

      {/* Indicators */}
      {data.bullish_indicators?.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-600">상승 지표</p>
          <div className="flex flex-wrap gap-1.5">
            {data.bullish_indicators.map((ind: any) => (
              <span key={ind.name} className="rounded bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-400">
                {ind.name} {ind.value > 0 ? '+' : ''}{(ind.value * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {data.bearish_indicators?.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-600">하락 지표</p>
          <div className="flex flex-wrap gap-1.5">
            {data.bearish_indicators.map((ind: any) => (
              <span key={ind.name} className="rounded bg-red-500/10 px-2 py-0.5 text-[11px] text-red-400">
                {ind.name} {(ind.value * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Conflicts warning */}
      {data.conflicts?.length > 0 && (
        <p className="text-[11px] text-zinc-500">
          {data.conflicts.join(', ')}에서 반대 신호 감지
        </p>
      )}

      {/* Memory refs */}
      {data.memory_refs > 0 && (
        <p className="text-[10px] text-zinc-600">
          과거 유사 상황 {data.memory_refs}건 참조
        </p>
      )}
    </div>
  );
}

/* ── Signal Strength Bar ─────────────────────────────────────── */

function SignalBar({ value, label }: { value: number; label?: string }) {
  // value is typically -1 to 1, normalize to 0-100
  const pct = Math.min(100, Math.max(0, (Math.abs(value) * 100)));
  const isStrong = pct > 70;
  const isMedium = pct > 40;

  return (
    <div className="space-y-1">
      {label && <p className="text-xs text-neutral-400">{label}</p>}
      <div className="h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${
            isStrong ? "bg-white" : isMedium ? "bg-zinc-400" : "bg-neutral-500"
          }`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: "easeOut", delay: 0.2 }}
        />
      </div>
    </div>
  );
}

/* ── Confidence Percentage Bar ───────────────────────────────── */

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);

  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 rounded-full bg-white/[0.06] overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-white"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1, ease: "easeOut", delay: 0.3 }}
        />
      </div>
      <span className="text-sm font-medium text-neutral-300 tabular-nums w-10 text-right">
        {pct}%
      </span>
    </div>
  );
}

/* ── Component Bars (top 3 from components dict) ─────────────── */

function ComponentBars({ components }: { components: Record<string, number> }) {
  const sorted = Object.entries(components)
    .sort(([, a], [, b]) => Math.abs(b) - Math.abs(a))
    .slice(0, 3);

  if (sorted.length === 0) return null;

  const maxVal = Math.max(...sorted.map(([, v]) => Math.abs(v)), 0.01);

  return (
    <div className="space-y-2 mt-3">
      <p className="text-xs text-neutral-400">주요 분석 요소</p>
      {sorted.map(([key, val]) => {
        const pct = Math.min(100, (Math.abs(val) / maxVal) * 100);
        const friendlyKey = key
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
        return (
          <div key={key} className="space-y-0.5">
            <div className="flex items-center justify-between">
              <span className="text-xs text-neutral-500">{friendlyKey}</span>
              <span className={`text-xs font-medium tabular-nums ${val >= 0 ? "text-emerald-400" : "text-red-500"}`}>
                {val >= 0 ? "+" : ""}{(val * 100).toFixed(0)}%
              </span>
            </div>
            <div className="h-1 w-full rounded-full bg-white/[0.06] overflow-hidden">
              <motion.div
                className={`h-full rounded-full ${val >= 0 ? "bg-emerald-400" : "bg-red-400"}`}
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.6, ease: "easeOut" }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Position Cards ──────────────────────────────────────────── */

function PositionCards({ positions }: { positions: Record<string, number> }) {
  const entries = Object.entries(positions);
  if (entries.length === 0) {
    return <p className="text-sm text-neutral-400 mt-3">현재 보유 자산이 없습니다</p>;
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-3">
      {entries.map(([asset, amount]) => (
        <motion.div
          key={asset}
          className="rounded-xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-3 hover:border-white/[0.10] transition-all duration-200"
          whileHover={{ scale: 1.02, y: -2 }}
        >
          <p className="text-xs text-neutral-400">보유</p>
          <p className="text-base font-semibold text-white mt-0.5">
            {friendlyAsset(asset)}
          </p>
          <p className="text-sm font-medium text-neutral-400 mt-1 tabular-nums">
            ${formatMoney(amount)}
          </p>
        </motion.div>
      ))}
    </div>
  );
}

/* ── Loading Skeleton ────────────────────────────────────────── */

function DashboardSkeleton() {
  return (
    <main className="max-w-5xl mx-auto space-y-8 px-4 pt-8">
      {/* Hero skeleton */}
      <div className="space-y-3">
        <div className="skeleton h-8 w-48" />
        <div className="skeleton h-5 w-72" />
      </div>
      {/* Cards skeleton */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-3">
            <div className="skeleton h-4 w-20" />
            <div className="skeleton h-10 w-32" />
          </div>
        ))}
      </div>
      {/* Decisions skeleton */}
      <div className="space-y-3">
        <div className="skeleton h-6 w-40" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-5 space-y-2">
            <div className="skeleton h-4 w-full" />
            <div className="skeleton h-3 w-2/3" />
          </div>
        ))}
      </div>
    </main>
  );
}

/* ── Main Dashboard ──────────────────────────────────────────── */

function DashboardContent() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const toast = useToast();

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
        toast.show("error", "대시보드 데이터를 불러오지 못했습니다");
      } else {
        setLastUpdated(Date.now());
      }
      setData(dash as DashboardData);
      setRecs(Array.isArray(rec) ? rec : []);
      const decArr = Array.isArray(dec) ? dec : [];
      setDecisions(decArr.slice(-5).reverse());
      setLoading(false);
    });
  }, [toast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const portfolio = data?.portfolio;
  const stats = data?.statistics;
  const topRec = recs[0];

  const agentMessage = useMemo(() => {
    if (!topRec) return "시장 데이터를 수집하고 있습니다. 잠시 후 분석이 시작됩니다.";
    const regime = friendlyRegime(topRec.regime);
    const method = friendlyFormula(topRec.formula_name);
    return `현재 시장은 ${regime} 국면으로, ${method}을 통해 매매 전략을 운용 중입니다.`;
  }, [topRec]);

  if (loading) return <DashboardSkeleton />;

  if (error) {
    return (
      <PageTransition>
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="text-sm text-zinc-500">데이터를 불러오는 중 오류가 발생했습니다</p>
          <button onClick={() => { setError(false); loadData(); }} className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black">
            다시 시도
          </button>
        </div>
      </PageTransition>
    );
  }

  const unrealizedPnl = portfolio?.unrealized_pnl ?? 0;
  const realizedPnl = portfolio?.realized_pnl ?? 0;

  return (
    <PageTransition>
      <main className="max-w-5xl mx-auto space-y-10 px-4 pt-6 pb-16">

        {/* ── Hero Section ────────────────────────────────── */}
        <FadeInView>
          <section className="relative overflow-hidden rounded-3xl border border-white/[0.06] bg-gradient-to-br from-white/[0.03] via-white/[0.02] to-white/[0.01] p-8 sm:p-12">
            {lastUpdated && (
              <span className="absolute top-4 right-4 text-[10px] text-zinc-600">
                마지막 업데이트: {new Date(lastUpdated).toLocaleTimeString("ko-KR")}
              </span>
            )}
            {/* Animated gradient background decoration */}
            <motion.div
              className="absolute -top-24 -right-24 w-64 h-64 rounded-full opacity-[0.04]"
              style={{ background: "radial-gradient(circle, #000 0%, transparent 70%)" }}
              animate={{
                scale: [1, 1.2, 1],
                x: [0, 20, 0],
                y: [0, -10, 0],
              }}
              transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
            />
            <motion.div
              className="absolute -bottom-16 -left-16 w-48 h-48 rounded-full opacity-[0.03]"
              style={{ background: "radial-gradient(circle, #000 0%, transparent 70%)" }}
              animate={{
                scale: [1, 1.15, 1],
                x: [0, -15, 0],
                y: [0, 15, 0],
              }}
              transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
            />

            <div className="relative z-10">
              <div className="flex items-center gap-3 mb-4">
                <motion.div
                  className="h-2 w-2 rounded-full bg-white"
                  animate={{ opacity: [1, 0.4, 1], boxShadow: ["0 0 6px rgba(255,255,255,0.6)", "0 0 2px rgba(255,255,255,0.2)", "0 0 6px rgba(255,255,255,0.6)"] }}
                  transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                />
                <span className="text-sm text-white font-medium">에이전트 활성</span>
              </div>

              <h1 className="text-3xl sm:text-4xl font-bold text-gradient-accent tracking-tight">
                오늘의 시장
              </h1>
              <motion.p
                className="mt-3 text-base sm:text-lg text-neutral-500 max-w-xl leading-relaxed"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.3, duration: 0.5 }}
              >
                {agentMessage}
              </motion.p>

              {topRec && (
                <motion.div
                  className="mt-6 flex flex-wrap gap-3"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.5, duration: 0.4 }}
                >
                  <span className="inline-flex items-center rounded-full bg-white/[0.08] text-white px-4 py-1.5 text-sm font-medium">
                    시장 상태 : {friendlyRegime(topRec.regime)}
                  </span>
                  <span className="inline-flex items-center rounded-full border border-white/[0.06] bg-white/[0.03] px-4 py-1.5 text-sm text-neutral-300">
                    분석 방식 : {friendlyFormula(topRec.formula_name)}
                  </span>
                </motion.div>
              )}
            </div>
          </section>
        </FadeInView>

        {/* ── Portfolio Summary Cards ─────────────────────── */}
        <StaggerContainer className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StaggerItem>
            <motion.div
              className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-6 hover:border-white/[0.06] transition-all duration-300 card-accent-hover"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-sm text-neutral-400 font-medium">총 투자금</p>
              <div className="mt-2 text-3xl font-bold text-white tabular-nums">
                $<AnimatedNumber value={portfolio?.total_exposure ?? 0} decimals={0} />
              </div>
              {portfolio?.positions && (
                <p className="mt-2 text-xs text-neutral-400">
                  {Object.keys(portfolio.positions).length}개 자산 보유 중
                </p>
              )}
            </motion.div>
          </StaggerItem>

          <StaggerItem>
            <motion.div
              className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-6 hover:border-white/[0.06] transition-all duration-300 card-accent-hover"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-sm text-neutral-400 font-medium">평가 손익</p>
              <div className={`mt-2 text-3xl font-bold tabular-nums ${
                unrealizedPnl >= 0 ? "text-emerald-400 drop-shadow-[0_0_8px_rgba(16,185,129,0.4)]" : "text-red-400 drop-shadow-[0_0_8px_rgba(239,68,68,0.4)]"
              }`}>
                {unrealizedPnl >= 0 ? "+" : ""}$<AnimatedNumber value={unrealizedPnl} decimals={0} />
              </div>
              <p className="mt-2 text-xs text-neutral-400">
                현재 보유 자산 기준
              </p>
            </motion.div>
          </StaggerItem>

          <StaggerItem>
            <motion.div
              className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-6 hover:border-white/[0.06] transition-all duration-300 card-accent-hover"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-sm text-neutral-400 font-medium">실현 손익</p>
              <div className={`mt-2 text-3xl font-bold tabular-nums ${
                realizedPnl >= 0 ? "text-emerald-400 drop-shadow-[0_0_8px_rgba(16,185,129,0.4)]" : "text-red-400 drop-shadow-[0_0_8px_rgba(239,68,68,0.4)]"
              }`}>
                {realizedPnl >= 0 ? "+" : ""}$<AnimatedNumber value={realizedPnl} decimals={0} />
              </div>
              {stats?.win_rate != null && (
                <p className="mt-2 text-xs text-neutral-400">
                  승률 {(stats.win_rate * 100).toFixed(0)}% / {stats?.trade_count ?? 0}회 거래
                </p>
              )}
            </motion.div>
          </StaggerItem>
        </StaggerContainer>

        {/* ── Market Chart ────────────────────────────────── */}
        <FadeInView delay={0.1}>
          <MarketChart asset="BTCUSDT" />
        </FadeInView>

        {/* ── Agent Status + Positions ────────────────────── */}
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Confidence & Signal */}
          <FadeInView>
            <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-6 h-full">
              <h2 className="text-lg font-semibold text-white">에이전트 분석 현황</h2>

              {topRec ? (
                <div className="mt-5 space-y-5">
                  {/* Confidence */}
                  <div>
                    <p className="text-sm text-neutral-500 mb-2">확신도</p>
                    <ConfidenceBar value={topRec.confidence} />
                  </div>

                  {/* Strategy reasoning */}
                  <div>
                    <p className="text-sm text-neutral-500 mb-1">분석 의견</p>
                    <p className="text-sm text-neutral-300 leading-relaxed">{topRec.reasoning}</p>
                  </div>

                  {/* Other recommendations */}
                  {recs.length > 1 && (
                    <div>
                      <p className="text-sm text-neutral-500 mb-2">대안 전략</p>
                      <div className="space-y-2">
                        {recs.slice(1, 3).map((r, i) => (
                          <motion.div
                            key={i}
                            className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 hover:bg-white/[0.03] transition-colors"
                            whileHover={{ x: 4 }}
                          >
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-medium text-neutral-300">{r.name}</span>
                              <span className="text-xs text-neutral-400 tabular-nums">
                                확신도 {(r.confidence * 100).toFixed(0)}%
                              </span>
                            </div>
                            <p className="mt-1 text-xs text-neutral-500 line-clamp-1">{r.reasoning}</p>
                          </motion.div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="mt-5 flex flex-col items-center justify-center py-8">
                  <motion.div
                    className="w-8 h-8 rounded-full border-2 border-white/[0.06] border-t-white"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
                  />
                  <p className="mt-4 text-sm text-neutral-400">시장을 분석하고 있습니다...</p>
                </div>
              )}
            </section>
          </FadeInView>

          {/* Positions */}
          <FadeInView delay={0.1}>
            <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-6 h-full">
              <h2 className="text-lg font-semibold text-white">보유 자산</h2>

              {portfolio?.positions && Object.keys(portfolio.positions).length > 0 ? (
                <PositionCards positions={portfolio.positions} />
              ) : (
                <div className="mt-5 flex flex-col items-center justify-center py-8">
                  <div className="w-12 h-12 rounded-full bg-white/[0.02] flex items-center justify-center">
                    <span className="text-neutral-300 text-xl">$</span>
                  </div>
                  <p className="mt-3 text-sm text-neutral-400">
                    아직 보유 자산이 없습니다
                  </p>
                  <p className="text-xs text-neutral-300 mt-1">
                    에이전트가 매수 신호를 감지하면 자동으로 투자합니다
                  </p>
                </div>
              )}
            </section>
          </FadeInView>
        </div>

        {/* ── Recent Decisions ────────────────────────────── */}
        <FadeInView delay={0.15}>
          <section>
            <h2 className="text-xl font-bold text-white mb-4 drop-shadow-[0_0_8px_rgba(255,255,255,0.3)]">최근 매매 결정</h2>

            {decisions.length === 0 ? (
              <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-8 text-center">
                <p className="text-sm text-neutral-400">
                  아직 매매 이력이 없습니다. 에이전트가 시장 데이터를 수집하면 자동으로 매매를 결정합니다.
                </p>
              </div>
            ) : (
              <StaggerContainer className="space-y-3">
                {decisions.map((d, i) => {
                  const act = friendlyAction(d.action);
                  return (
                    <StaggerItem key={d.decision_id ?? i}>
                      <motion.div
                        className="rounded-2xl border border-white/[0.06] bg-white/[0.03] backdrop-blur-sm p-5 hover:border-white/[0.06] transition-all duration-300"
                        whileHover={{ y: -2 }}
                      >
                        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                          {/* Asset + Action */}
                          <div className="flex items-center gap-3 flex-1">
                            <span className="text-base font-semibold text-white">
                              {friendlyAsset(d.asset)}
                            </span>
                            <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${act.bg} ${act.color}`}>
                              {act.label}
                            </span>
                            {d.threshold_crossed && (
                              <span className="inline-flex items-center rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-400 px-2.5 py-0.5 text-xs font-medium">
                                매매 신호 발생
                              </span>
                            )}
                          </div>

                          {/* Signal strength + Time */}
                          <div className="flex items-center gap-4 sm:flex-shrink-0">
                            <div className="w-24">
                              <SignalBar value={d.signal_score} label="시장 신호" />
                            </div>
                            {d.timestamp && (
                              <span className="text-xs text-neutral-400 whitespace-nowrap">
                                {timeAgo(d.timestamp)}
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Reasoning */}
                        {d.reasoning && (
                          <div className="mt-3">
                            <ReasoningCard reasoning={d.reasoning} />
                          </div>
                        )}

                        {/* Components as bars */}
                        {d.components && Object.keys(d.components).length > 0 && (
                          <ComponentBars components={d.components} />
                        )}
                      </motion.div>
                    </StaggerItem>
                  );
                })}
              </StaggerContainer>
            )}
          </section>
        </FadeInView>
      </main>
    </PageTransition>
  );
}

/* ── Page Export ──────────────────────────────────────────────── */

export default function DashboardPage() {
  return (
    <AuthGuard>
      <DashboardContent />
    </AuthGuard>
  );
}
