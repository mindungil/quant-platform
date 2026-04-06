"use client";

import { useEffect, useState, useMemo } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
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
      return { label: "매수", color: "text-emerald-700", bg: "bg-emerald-50 border-emerald-200" };
    case "SELL":
      return { label: "매도", color: "text-red-600", bg: "bg-red-50 border-red-200" };
    default:
      return { label: "관망", color: "text-neutral-500", bg: "bg-neutral-50 border-neutral-200" };
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

/* ── Signal Strength Bar ─────────────────────────────────────── */

function SignalBar({ value, label }: { value: number; label?: string }) {
  // value is typically -1 to 1, normalize to 0-100
  const pct = Math.min(100, Math.max(0, (Math.abs(value) * 100)));
  const isStrong = pct > 70;
  const isMedium = pct > 40;

  return (
    <div className="space-y-1">
      {label && <p className="text-xs text-neutral-400">{label}</p>}
      <div className="h-1.5 w-full rounded-full bg-neutral-100 overflow-hidden">
        <motion.div
          className={`h-full rounded-full ${
            isStrong ? "bg-neutral-900" : isMedium ? "bg-neutral-500" : "bg-neutral-300"
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
      <div className="flex-1 h-2 rounded-full bg-neutral-100 overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-neutral-900"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1, ease: "easeOut", delay: 0.3 }}
        />
      </div>
      <span className="text-sm font-medium text-neutral-700 tabular-nums w-10 text-right">
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
              <span className={`text-xs font-medium tabular-nums ${val >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                {val >= 0 ? "+" : ""}{(val * 100).toFixed(0)}%
              </span>
            </div>
            <div className="h-1 w-full rounded-full bg-neutral-100 overflow-hidden">
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
          className="rounded-xl border border-neutral-200 bg-white/60 backdrop-blur-sm p-3 hover:shadow-md hover:border-neutral-300 transition-all duration-200"
          whileHover={{ scale: 1.02, y: -2 }}
        >
          <p className="text-xs text-neutral-400">보유</p>
          <p className="text-base font-semibold text-neutral-900 mt-0.5">
            {friendlyAsset(asset)}
          </p>
          <p className="text-sm font-medium text-neutral-600 mt-1 tabular-nums">
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
          <div key={i} className="rounded-2xl border border-neutral-100 bg-white p-6 space-y-3">
            <div className="skeleton h-4 w-20" />
            <div className="skeleton h-10 w-32" />
          </div>
        ))}
      </div>
      {/* Decisions skeleton */}
      <div className="space-y-3">
        <div className="skeleton h-6 w-40" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="rounded-2xl border border-neutral-100 bg-white p-5 space-y-2">
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

  useEffect(() => {
    Promise.all([
      gatewayFetch("/dashboard").catch(() => null),
      gatewayFetch("/recommendations/BTCUSDT").catch(() => []),
      gatewayFetch("/decisions/history/BTCUSDT").catch(() => []),
    ]).then(([dash, rec, dec]) => {
      setData(dash as DashboardData);
      setRecs(Array.isArray(rec) ? rec : []);
      const decArr = Array.isArray(dec) ? dec : [];
      setDecisions(decArr.slice(-5).reverse());
      setLoading(false);
    });
  }, []);

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

  const unrealizedPnl = portfolio?.unrealized_pnl ?? 0;
  const realizedPnl = portfolio?.realized_pnl ?? 0;

  return (
    <PageTransition>
      <main className="max-w-5xl mx-auto space-y-10 px-4 pt-6 pb-16">

        {/* ── Hero Section ────────────────────────────────── */}
        <FadeInView>
          <section className="relative overflow-hidden rounded-3xl border border-neutral-100 bg-gradient-to-br from-neutral-50 via-white to-neutral-50 p-8 sm:p-12">
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
                  className="h-2 w-2 rounded-full bg-cyan-400"
                  animate={{ opacity: [1, 0.4, 1], boxShadow: ["0 0 6px rgba(6,182,212,0.6)", "0 0 2px rgba(6,182,212,0.2)", "0 0 6px rgba(6,182,212,0.6)"] }}
                  transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                />
                <span className="text-sm text-cyan-500 font-medium">에이전트 활성</span>
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
                  <span className="inline-flex items-center rounded-full bg-neutral-900 text-white px-4 py-1.5 text-sm font-medium">
                    시장 상태 : {friendlyRegime(topRec.regime)}
                  </span>
                  <span className="inline-flex items-center rounded-full border border-neutral-300 bg-white px-4 py-1.5 text-sm text-neutral-600">
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
              className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-6 hover:shadow-lg hover:border-neutral-200 transition-all duration-300 card-accent-hover"
              whileHover={{ scale: 1.02, y: -4 }}
            >
              <p className="text-sm text-neutral-400 font-medium">총 투자금</p>
              <div className="mt-2 text-3xl font-bold text-neutral-900 tabular-nums">
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
              className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-6 hover:shadow-lg hover:border-neutral-200 transition-all duration-300 card-accent-hover"
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
              className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-6 hover:shadow-lg hover:border-neutral-200 transition-all duration-300 card-accent-hover"
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
            <section className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-6 h-full">
              <h2 className="text-lg font-semibold text-neutral-900">에이전트 분석 현황</h2>

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
                    <p className="text-sm text-neutral-700 leading-relaxed">{topRec.reasoning}</p>
                  </div>

                  {/* Other recommendations */}
                  {recs.length > 1 && (
                    <div>
                      <p className="text-sm text-neutral-500 mb-2">대안 전략</p>
                      <div className="space-y-2">
                        {recs.slice(1, 3).map((r, i) => (
                          <motion.div
                            key={i}
                            className="rounded-xl border border-neutral-100 bg-neutral-50/50 p-3 hover:bg-neutral-50 transition-colors"
                            whileHover={{ x: 4 }}
                          >
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-medium text-neutral-700">{r.name}</span>
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
                    className="w-8 h-8 rounded-full border-2 border-neutral-200 border-t-neutral-900"
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
            <section className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-6 h-full">
              <h2 className="text-lg font-semibold text-neutral-900">보유 자산</h2>

              {portfolio?.positions && Object.keys(portfolio.positions).length > 0 ? (
                <PositionCards positions={portfolio.positions} />
              ) : (
                <div className="mt-5 flex flex-col items-center justify-center py-8">
                  <div className="w-12 h-12 rounded-full bg-neutral-50 flex items-center justify-center">
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
            <h2 className="text-lg font-semibold text-gradient-accent mb-4">최근 매매 결정</h2>

            {decisions.length === 0 ? (
              <div className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-8 text-center">
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
                        className="rounded-2xl border border-neutral-100 bg-white/70 backdrop-blur-sm p-5 hover:shadow-md hover:border-neutral-200 transition-all duration-300"
                        whileHover={{ y: -2 }}
                      >
                        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                          {/* Asset + Action */}
                          <div className="flex items-center gap-3 flex-1">
                            <span className="text-base font-semibold text-neutral-900">
                              {friendlyAsset(d.asset)}
                            </span>
                            <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${act.bg} ${act.color}`}>
                              {act.label}
                            </span>
                            {d.threshold_crossed && (
                              <span className="inline-flex items-center rounded-full bg-amber-50 border border-amber-200 text-amber-700 px-2.5 py-0.5 text-xs font-medium">
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
                          <p className="mt-3 text-sm text-neutral-500 leading-relaxed">
                            {d.reasoning}
                          </p>
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
