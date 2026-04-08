"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { useToast } from "../../components/toast";
import { parseReasoning, cleanReasoning, formatIndicatorName, formatStrategyDescription, formatRegime, formatConfidence, beginnerAction, beginnerIndicatorName, beginnerIndicatorStrength } from "../../lib/reasoning";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  AnimatedNumber,
  motion,
} from "../../components/motion";
import { MarketChart } from "../../components/market-chart";
import { IconUp, IconDown, IconPause, IconEmpty } from "../../components/icons";

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

/* ── Decision Card (compact, scannable) ──────────────────────── */

function DecisionCard({ decision }: { decision: Decision }) {
  const { structured, text } = parseReasoning(decision.reasoning || "");
  const action = friendlyAction(decision.action);
  const timeStr = decision.timestamp ? timeAgo(decision.timestamp) : "";

  // Use structured data if available, otherwise parse from components
  const indicators = structured?.bullish_indicators || [];
  const bearish = structured?.bearish_indicators || [];
  const conflicts = structured?.conflicts || [];
  const regime = structured?.regime || "";
  const strength = structured?.strength || "";
  const memRefs = structured?.memory_refs || 0;

  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 hover:bg-white/[0.04] transition-all duration-150">
      {/* Header: Asset + Action + Time */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-zinc-50">{friendlyAsset(decision.asset)}</span>
          <span className={`rounded-md px-2 py-0.5 text-[11px] font-medium border ${decision.action === "BUY" ? "text-green-500 bg-green-500/10 border-green-500/15" : decision.action === "SELL" ? "text-red-500 bg-red-500/10 border-red-500/15" : "text-zinc-400 bg-white/[0.05] border-white/[0.06]"}`}>
            {action.label}
          </span>
        </div>
        <span className="text-[11px] text-zinc-500">{timeStr}</span>
      </div>

      {/* Subtitle: Regime + Strength */}
      <p className="mt-1.5 text-xs text-zinc-500">
        {formatRegime(regime)}{strength ? ` · 시그널 ${strength}` : ""}
      </p>

      {/* Indicators (inline chips — beginner-friendly) */}
      {indicators.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {indicators.slice(0, 4).map((ind: any) => (
            <span key={ind.name} className="rounded-md border border-green-500/15 bg-green-500/10 px-1.5 py-0.5 text-[10px] font-medium text-green-500">
              {beginnerIndicatorName(ind.name)}: {beginnerIndicatorStrength(ind.value)}
            </span>
          ))}
          {bearish.slice(0, 2).map((ind: any) => (
            <span key={ind.name} className="rounded-md border border-red-500/15 bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-500">
              {beginnerIndicatorName(ind.name)}: {beginnerIndicatorStrength(ind.value)}
            </span>
          ))}
        </div>
      )}

      {/* Conflicts + Memory (compact footer) */}
      {(conflicts.length > 0 || memRefs > 0) && (
        <div className="mt-2 flex items-center gap-3 text-[10px] text-zinc-500">
          {conflicts.length > 0 && <span>{conflicts.map((c: string) => formatIndicatorName(c)).join(", ")} \uBC18\uB300 \uC2E0\uD638</span>}
          {memRefs > 0 && <span>\uC720\uC0AC {memRefs}\uAC74 \uCC38\uC870</span>}
        </div>
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
      {label && <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">{label}</p>}
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
      <span className="font-mono text-sm font-medium tabular-nums text-zinc-50 w-10 text-right">
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
      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">주요 분석 요소</p>
      {sorted.map(([key, val]) => {
        const pct = Math.min(100, (Math.abs(val) / maxVal) * 100);
        const friendlyKey = formatIndicatorName(key);
        return (
          <div key={key} className="space-y-0.5">
            <div className="flex items-center justify-between">
              <span className="text-xs text-zinc-400">{friendlyKey}</span>
              <span className={`font-mono text-xs font-medium tabular-nums ${val >= 0 ? "text-green-500" : "text-red-500"}`}>
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
    return <p className="text-sm text-zinc-400 leading-relaxed mt-3">현재 보유 자산이 없습니다</p>;
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-3">
      {entries.map(([asset, amount]) => (
        <motion.div
          key={asset}
          className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 hover:bg-white/[0.04] hover:border-white/[0.10] transition-all duration-150"
          whileHover={{ scale: 1.02, y: -2 }}
        >
          <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">보유</p>
          <p className="text-sm font-medium text-zinc-200 mt-0.5">
            {friendlyAsset(asset)}
          </p>
          <p className="font-mono text-sm font-medium tabular-nums text-zinc-50 mt-1">
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
  const [hindsight, setHindsight] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const toast = useToast();

  const loadData = useCallback(() => {
    setLoading(true);
    setError(false);
    gatewayFetch("/statistics/hindsight/BTCUSDT").then(setHindsight).catch(() => {});
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
              <span className="absolute top-4 right-4 text-[10px] text-zinc-500">
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
              {/* Agent status card — icon + action only */}
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                <div className="flex items-center gap-3">
                  <span>
                    {decisions[0]?.action === "BUY" ? <IconUp /> : decisions[0]?.action === "SELL" ? <IconDown /> : <IconPause />}
                  </span>
                  <p className="text-sm font-medium text-zinc-50">
                    {beginnerAction(decisions[0]?.action).text}
                  </p>
                </div>
              </div>

              {topRec && (
                <motion.div
                  className="mt-4 flex flex-wrap gap-3"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.5, duration: 0.4 }}
                >
                  <span className="text-xs font-medium text-zinc-400 bg-white/[0.05] border border-white/[0.06] rounded-md px-2 py-0.5">
                    시장 상태 : {formatRegime(topRec.regime)}
                  </span>
                  <span className="text-xs font-medium text-zinc-400 bg-white/[0.05] border border-white/[0.06] rounded-md px-2 py-0.5">
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
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 hover:bg-white/[0.04] hover:border-white/[0.10] transition-all duration-150"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">총 투자금</p>
              <div className="mt-2 text-3xl font-bold tracking-tighter tabular-nums text-zinc-50">
                $<AnimatedNumber value={portfolio?.total_exposure ?? 0} decimals={0} />
              </div>
              {portfolio?.positions && (
                <p className="mt-2 text-xs font-medium text-zinc-500">
                  {Object.keys(portfolio.positions).length}개 자산 보유 중
                </p>
              )}
            </motion.div>
          </StaggerItem>

          <StaggerItem>
            <motion.div
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 hover:bg-white/[0.04] hover:border-white/[0.10] transition-all duration-150"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">평가 손익</p>
              <div className={`mt-2 text-3xl font-bold tracking-tighter tabular-nums ${
                unrealizedPnl >= 0 ? "text-green-500" : "text-red-500"
              }`}>
                {unrealizedPnl >= 0 ? "+" : ""}$<AnimatedNumber value={unrealizedPnl} decimals={0} />
              </div>
              <p className="mt-2 text-xs font-medium text-zinc-500">
                현재 보유 자산 기준
              </p>
            </motion.div>
          </StaggerItem>

          <StaggerItem>
            <motion.div
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 hover:bg-white/[0.04] hover:border-white/[0.10] transition-all duration-150"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">실현 손익</p>
              <div className={`mt-2 text-3xl font-bold tracking-tighter tabular-nums ${
                realizedPnl >= 0 ? "text-green-500" : "text-red-500"
              }`}>
                {realizedPnl >= 0 ? "+" : ""}$<AnimatedNumber value={realizedPnl} decimals={0} />
              </div>
              {stats?.win_rate != null && (
                <p className="mt-2 text-xs font-medium text-zinc-500">
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

        {/* ── Agent Accuracy Widget ────────────────────────── */}
        <FadeInView delay={0.12}>
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 animate-card-reveal">
            <h3 className="text-base font-semibold tracking-tight text-zinc-50">AI 분석 정확도</h3>

            {hindsight ? (
              <div className="mt-4">
                <div className="flex items-center justify-between text-xs text-zinc-400">
                  <span>{hindsight.total.toLocaleString()}회 분석</span>
                  <span>적중률 {(hindsight.accuracy * 100).toFixed(0)}%</span>
                </div>
                <div className="mt-2 h-2 rounded-full bg-white/[0.06]">
                  <div className="h-full rounded-full bg-green-500 transition-all duration-700" style={{width: `${Math.min(hindsight.accuracy * 100, 100)}%`}} />
                </div>
              </div>
            ) : (
              <p className="mt-4 text-sm text-zinc-500">데이터 수집 중</p>
            )}
          </section>
        </FadeInView>

        {/* ── Agent Status + Positions ────────────────────── */}
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Confidence & Signal */}
          <FadeInView>
            <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 h-full hover:bg-white/[0.04] hover:border-white/[0.10] transition-all duration-150 animate-card-reveal">
              <h2 className="text-base font-semibold tracking-tight text-zinc-50">에이전트 분석 현황</h2>

              {topRec ? (
                <div className="mt-5 space-y-5">
                  {/* Confidence */}
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500 mb-2">확신도</p>
                    <ConfidenceBar value={topRec.confidence} />
                  </div>

                  {/* Strategy reasoning — formatted */}
                  <div>
                    <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500 mb-1">분석 의견</p>
                    <p className="text-sm text-zinc-400 leading-relaxed">{formatStrategyDescription(cleanReasoning(topRec.reasoning))}</p>
                  </div>

                  {/* Regime tag */}
                  <div className="flex gap-2">
                    <span className="rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-zinc-500">{formatRegime(topRec.regime)}</span>
                  </div>

                  {/* Other recommendations — formatted cards */}
                  {recs.length > 1 && (
                    <div>
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500 mb-2">대안 전략</p>
                      <div className="space-y-2">
                        {recs.slice(1, 3).map((r, i) => {
                          const conf = formatConfidence(r.confidence);
                          return (
                            <div key={i} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                              <div className="flex items-center justify-between">
                                <span className="text-sm font-medium text-zinc-200">{formatIndicatorName(r.name)}</span>
                                <div className="flex items-center gap-1.5">
                                  <div className={`h-1.5 w-1.5 rounded-full ${conf.level === 'high' ? 'bg-green-500' : conf.level === 'medium' ? 'bg-amber-500' : 'bg-zinc-500'}`} />
                                  <span className="text-[11px] font-medium tabular-nums text-zinc-400">{Math.round(r.confidence * 100)}%</span>
                                </div>
                              </div>
                              <p className="mt-1.5 text-xs text-zinc-500">{formatStrategyDescription(cleanReasoning(r.reasoning))}</p>
                              <div className="mt-2 flex gap-2">
                                <span className="rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-zinc-500">{formatRegime(r.regime)}</span>
                              </div>
                            </div>
                          );
                        })}
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
            <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 h-full hover:bg-white/[0.04] hover:border-white/[0.10] transition-all duration-150 animate-card-reveal delay-100">
              <h2 className="text-base font-semibold tracking-tight text-zinc-50">보유 자산</h2>

              {portfolio?.positions && Object.keys(portfolio.positions).length > 0 ? (
                <PositionCards positions={portfolio.positions} />
              ) : (
                <div className="mt-5 flex flex-col items-center py-8">
                  <IconEmpty />
                  <p className="mt-3 text-sm text-zinc-400">보유 자산 없음</p>
                </div>
              )}
            </section>
          </FadeInView>
        </div>

        {/* ── Recent Decisions ────────────────────────────── */}
        <FadeInView delay={0.15}>
          <section>
            <h2 className="text-base font-semibold tracking-tight text-zinc-50 mb-4">최근 매매 결정</h2>

            {decisions.length === 0 ? (
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-8 flex flex-col items-center text-center">
                <IconEmpty />
                <p className="mt-3 text-sm text-zinc-400">아직 매매 기록 없음</p>
              </div>
            ) : (
              <StaggerContainer className="space-y-3">
                {decisions.map((d, i) => (
                  <StaggerItem key={d.decision_id ?? i}>
                    <DecisionCard decision={d} />
                  </StaggerItem>
                ))}
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
