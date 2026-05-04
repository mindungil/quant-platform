"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { ErrorBoundary, EmptyState, LoadingSkeleton } from "../../components/error-boundary";
import { formatIndicatorName, beginnerFriendly, beginnerIndicatorName } from "../../lib/reasoning";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  AnimatedNumber,
  motion,
  AnimatePresence,
} from "../../components/motion";

/* ── Types ──────────────────────────────────────────────────── */

interface StrategyStats {
  trade_count?: number;
  total_return?: number;
  win_rate?: number;
  sharpe?: number;
  sortino?: number;
  max_drawdown?: number;
  profit_factor?: number;
  expectancy?: number;
  drift_detected?: boolean;
  recent_trade_pnls?: number[];
}

interface Strategy {
  id: string;
  name: string;
  asset_type: string;
  status: string;
  indicators?: string[];
  version?: string;
  thresholds?: Record<string, number>;
  user_id?: string;
}

interface Recommendation {
  name: string;
  description: string;
  formula_name: string;
  regime: string;
  confidence: number;
  reasoning: string;
}

/* ── Drift badge ────────────────────────────────────────────── */

function DriftBadge({ stats }: { stats: StrategyStats | null }) {
  if (!stats || stats.trade_count === 0) {
    return (
      <span className="badge bg-neutral-700/50 text-[#a1a1a1]">
        데이터 없음
      </span>
    );
  }

  const totalReturn = stats.total_return ?? 0;
  const driftDetected = stats.drift_detected ?? false;
  const maxDrawdown = stats.max_drawdown ?? 0;

  // Determine drift level: red / yellow / green
  let level: "red" | "yellow" | "green" = "green";
  if (driftDetected && maxDrawdown > 0.1) {
    level = "red";
  } else if (driftDetected || maxDrawdown > 0.05) {
    level = "yellow";
  }

  const colors = {
    green: "bg-emerald-500/15 text-emerald-400",
    yellow: "bg-amber-500/15 text-amber-400",
    red: "bg-red-500/15 text-red-400",
  };

  const labels = {
    green: "정상",
    yellow: "주의",
    red: "이탈",
  };

  return (
    <span className={`badge ${colors[level]}`}>
      <span
        className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${
          level === "green"
            ? "bg-emerald-400"
            : level === "yellow"
              ? "bg-amber-400"
              : "bg-red-400"
        }`}
      />
      {labels[level]}
    </span>
  );
}

/* ── Baseline comparison mini bar ───────────────────────────── */

function BaselineComparison({
  live,
  baseline,
  label,
}: {
  live: number;
  baseline: number;
  label: string;
}) {
  const maxVal = Math.max(Math.abs(live), Math.abs(baseline), 0.01);
  const livePct = (Math.abs(live) / maxVal) * 100;
  const basePct = (Math.abs(baseline) / maxVal) * 100;

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">
        {label}
      </p>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <span className="w-10 text-[10px] text-[#a1a1a1]">라이브</span>
          <div className="flex-1 rounded-full bg-[#16161a] h-2 overflow-hidden">
            <motion.div
              className={`h-2 rounded-full ${live >= 0 ? "bg-emerald-500" : "bg-red-400"}`}
              initial={{ width: 0 }}
              animate={{ width: `${Math.min(livePct, 100)}%` }}
              transition={{ duration: 0.6, ease: "easeOut" }}
            />
          </div>
          <span className={`w-16 text-right font-mono text-[11px] ${live >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {live >= 0 ? "+" : ""}{(live * 100).toFixed(2)}%
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-10 text-[10px] text-[#a1a1a1]">기준</span>
          <div className="flex-1 rounded-full bg-[#16161a] h-2 overflow-hidden">
            <motion.div
              className="h-2 rounded-full bg-zinc-500"
              initial={{ width: 0 }}
              animate={{ width: `${Math.min(basePct, 100)}%` }}
              transition={{ duration: 0.6, delay: 0.1, ease: "easeOut" }}
            />
          </div>
          <span className="w-16 text-right font-mono text-[11px] text-[#a1a1a1]">
            {baseline >= 0 ? "+" : ""}{(baseline * 100).toFixed(2)}%
          </span>
        </div>
      </div>
    </div>
  );
}

/* ── Main content ───────────────────────────────────────────── */

function StrategiesContent() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [stats, setStats] = useState<StrategyStats | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [baselines, setBaselines] = useState<{ return: number; win_rate: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stratList, statsData, recsData, baselineData] = await Promise.all([
        gatewayFetch("/strategies/active?asset_type=crypto").catch(() => []),
        gatewayFetch("/statistics").catch(() => null),
        gatewayFetch("/recommendations/BTCUSDT").catch(() => []),
        gatewayFetch("/strategies/baselines").catch(() => null),
      ]);
      setStrategies(Array.isArray(stratList) ? stratList : stratList ? [stratList] : []);
      setStats(statsData);
      setRecommendations(Array.isArray(recsData) ? recsData : []);
      if (baselineData) {
        const bd = baselineData as { expected_return?: number; win_rate?: number };
        setBaselines({
          return: bd.expected_return ?? 0,
          win_rate: bd.win_rate ?? 0.5,
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "데이터를 불러올 수 없습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <main className="grid gap-6">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-white">AI 분석 전략</h2>
          <p className="mt-1 text-sm text-[#a1a1a1] leading-relaxed">데이터를 불러오고 있어요...</p>
        </div>
        <LoadingSkeleton rows={4} />
      </main>
    );
  }

  if (error) {
    return (
      <main className="grid gap-6">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-white">AI 분석 전략</h2>
        </div>
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 p-6 text-center">
          <p className="text-sm text-red-400">{error}</p>
          <button onClick={load} className="btn-secondary mt-3">
            다시 시도
          </button>
        </div>
      </main>
    );
  }

  const totalReturn = stats?.total_return ?? 0;
  const winRate = stats?.win_rate ?? 0;
  const tradeCount = stats?.trade_count ?? 0;
  const baselineReturn = baselines?.return ?? 0; // backtest baseline (expected return) from registry
  const baselineWinRate = baselines?.win_rate ?? 0.5; // win-rate baseline from registry
  const driftDetected = stats?.drift_detected ?? false;

  return (
    <PageTransition>
      <main className="grid gap-6">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-white">AI 분석 전략</h2>
          <p className="mt-1 text-xs text-[#a1a1a1]">AI 분석 방법 및 성과</p>
        </div>

        {/* Drift Overview */}
        <FadeInView>
          <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold tracking-tight text-white">
                  전략 상태
                </h3>
                <p className="mt-0.5 text-xs text-[#a1a1a1]">실시간 전략 모니터링</p>
              </div>
              <DriftBadge stats={stats} />
            </div>
            {tradeCount > 0 ? (
              <div className="mt-5 grid gap-5 md:grid-cols-2">
                <BaselineComparison
                  live={totalReturn}
                  baseline={baselineReturn}
                  label="총 수익률"
                />
                <BaselineComparison
                  live={winRate}
                  baseline={baselineWinRate}
                  label="승률"
                />
              </div>
            ) : (
              <p className="mt-4 text-sm text-[#a1a1a1]">거래 데이터 없음</p>
            )}
          </section>
        </FadeInView>

        {/* Performance Summary */}
        <FadeInView delay={0.05}>
          <StaggerContainer className="grid gap-4 sm:grid-cols-4">
            {[
              {
                label: "총 수익률",
                value: totalReturn * 100,
                suffix: "%",
                color: totalReturn >= 0 ? "text-emerald-400" : "text-red-400",
              },
              {
                label: "적중률",
                value: winRate * 100,
                suffix: "%",
                color: "text-white",
              },
              {
                label: "분석 횟수",
                value: tradeCount,
                suffix: "건",
                color: "text-white",
              },
              {
                label: "최대 손실폭",
                value: (stats?.max_drawdown ?? 0) * 100,
                suffix: "%",
                color: "text-red-400",
              },
            ].map((m, i) => (
              <StaggerItem key={i}>
                <div className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-5">
                  <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">
                    {m.label}
                  </p>
                  <p className={`mt-2 font-mono text-2xl font-semibold tracking-tighter tabular-nums ${m.color === "text-white" ? "text-white" : m.color}`}>
                    <AnimatedNumber value={m.value} decimals={m.suffix === "건" ? 0 : 1} />
                    <span className="text-sm text-[#a1a1a1]">{m.suffix}</span>
                  </p>
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
        </FadeInView>

        {/* Active Strategies */}
        <FadeInView delay={0.1}>
          <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6">
            <h3 className="text-base font-semibold tracking-tight text-white">현재 사용 중인 분석 방법</h3>
            <p className="mt-0.5 text-xs text-[#a1a1a1]">활성 분석 방법</p>

            {strategies.length === 0 ? (
              <EmptyState
                title="활성 전략 없음"
                description="분석 시작 시 자동 등록"
              />
            ) : (
              <StaggerContainer className="mt-4 space-y-3">
                {strategies.map((strat) => (
                  <StaggerItem key={strat.id}>
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-medium text-zinc-200">{
                            strat.name.includes("composite_adaptive") ? "종합 분석" :
                            strat.name.includes("factor_ensemble") ? "AI 팩터 분석" :
                            strat.name.includes("momentum") ? "모멘텀 분석" :
                            strat.name.includes("mean_revert") ? "평균 회귀 분석" :
                            strat.name.includes("trend") ? "추세 추종 분석" :
                            strat.name.includes("breakout") ? "돌파 분석" :
                            beginnerFriendly(strat.name)
                          }</p>
                          <p className="mt-0.5 text-xs text-[#a1a1a1]">
                            {strat.asset_type === "crypto" ? "암호화폐" : strat.asset_type}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <DriftBadge stats={stats} />
                          <span
                            className={`badge ${
                              strat.status === "ACTIVE"
                                ? "bg-emerald-500/15 text-emerald-400"
                                : strat.status === "SHADOW"
                                  ? "bg-[#1a1a1a] text-[#a1a1a1]"
                                  : "bg-neutral-700/50 text-[#a1a1a1]"
                            }`}
                          >
                            {strat.status}
                          </span>
                        </div>
                      </div>
                      {strat.indicators && strat.indicators.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {strat.indicators.map((ind) => (
                            <span
                              key={ind}
                              className="text-xs font-medium text-[#a1a1a1] bg-[#16161a] border border-[#2e2e2e] rounded-md px-2 py-0.5"
                            >
                              {beginnerIndicatorName(ind)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            )}
          </section>
        </FadeInView>

        {/* Expert Metrics */}
        {stats && tradeCount > 0 && (
          <FadeInView delay={0.15}>
            <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6">
              <h3 className="text-base font-semibold tracking-tight text-white">상세 성과 지표</h3>
              <p className="mt-0.5 text-xs text-[#a1a1a1]">상세 지표</p>
              <StaggerContainer className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  { label: "위험 대비 수익률", value: stats.sharpe, fmt: 2 },
                  { label: "하락 대비 수익률", value: stats.sortino, fmt: 2 },
                  { label: "수익 배수", value: stats.profit_factor, fmt: 2 },
                  { label: "평균 기대 수익", value: stats.expectancy, fmt: 4 },
                ].map((m, i) => (
                  <StaggerItem key={i}>
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="text-[10px] font-medium uppercase tracking-wider text-[#a1a1a1]">
                        {m.label}
                      </p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-white">
                        {m.value != null ? m.value.toFixed(m.fmt) : "--"}
                      </p>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            </section>
          </FadeInView>
        )}

        {/* Recommendations */}
        {recommendations.length > 0 && (
          <FadeInView delay={0.2}>
            <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6">
              <h3 className="text-base font-semibold tracking-tight text-white">AI 추천 전략</h3>
              <p className="mt-0.5 text-xs text-[#a1a1a1]">현재 시장 기반 추천</p>
              <StaggerContainer className="mt-4 space-y-3">
                {recommendations.map((rec, i) => (
                  <StaggerItem key={i}>
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium text-zinc-200">{beginnerFriendly(rec.name)}</p>
                        <span className="text-xs font-medium tabular-nums text-white bg-[#16161a] border border-[#2e2e2e] rounded-md px-2 py-0.5">
                          신뢰도 {(rec.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-[#a1a1a1] leading-relaxed">{beginnerFriendly(rec.description)}</p>
                      <div className="mt-2 flex gap-2">
                        <span className="rounded-md bg-[#16161a] px-2 py-0.5 text-[10px] text-[#a1a1a1]">
                          {beginnerFriendly(rec.formula_name)}
                        </span>
                        <span className="rounded-md bg-[#16161a] px-2 py-0.5 text-[10px] text-[#a1a1a1]">
                          {beginnerFriendly(rec.regime)}
                        </span>
                      </div>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            </section>
          </FadeInView>
        )}
      </main>
    </PageTransition>
  );
}

export default function StrategiesPage() {
  return (
    <AuthGuard>
      <ErrorBoundary>
        <StrategiesContent />
      </ErrorBoundary>
    </AuthGuard>
  );
}
