"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { useToast } from "../../components/toast";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  AnimatedNumber,
  Expandable,
  motion,
} from "../../components/motion";

/* ── asset name mapping ──────────────────────────────────────── */
const ASSET_NAMES: Record<string, string> = {
  BTCUSDT: "비트코인",
  ETHUSDT: "이더리움",
  SOLUSDT: "솔라나",
  XRPUSDT: "리플",
  DOGEUSDT: "도지코인",
  ADAUSDT: "에이다",
  BNBUSDT: "바이낸스코인",
};

function friendlyAsset(raw: string): string {
  return ASSET_NAMES[raw.toUpperCase()] ?? raw;
}

/* ── animated bar (grows from 0) ─────────────────────────────── */
function PnlBar({ value, maxAbs, index }: { value: number; maxAbs: number; index: number }) {
  const pct = Math.max((Math.abs(value) / maxAbs) * 100, 4);
  return (
    <motion.div
      className="flex-1 flex flex-col items-center justify-end h-full group relative"
    >
      <motion.div
        className={`w-full rounded-t ${value >= 0 ? "bg-green-500" : "bg-red-400"}`}
        initial={{ height: 0 }}
        animate={{ height: `${pct}%` }}
        transition={{ duration: 0.5, delay: index * 0.03, ease: "easeOut" }}
      />
      {/* tooltip on hover */}
      <div className="absolute -top-7 hidden group-hover:block rounded bg-neutral-800 px-2 py-0.5 text-[10px] text-white whitespace-nowrap">
        {value >= 0 ? "+" : ""}{value.toFixed(2)}
      </div>
    </motion.div>
  );
}

/* ── concentration bar with colour ───────────────────────────── */
const BAR_COLORS = [
  "bg-white",
  "bg-zinc-300",
  "bg-zinc-400",
  "bg-zinc-500",
  "bg-zinc-600",
  "bg-zinc-200",
  "bg-neutral-400",
];

function ConcentrationBar({
  asset,
  weight,
  index,
}: {
  asset: string;
  weight: number;
  index: number;
}) {
  const color = BAR_COLORS[index % BAR_COLORS.length];
  return (
    <motion.div
      className="flex items-center gap-3"
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, delay: index * 0.06 }}
    >
      <span className="w-24 text-sm font-medium text-neutral-300 truncate">
        {friendlyAsset(asset)}
      </span>
      <div className="flex-1 rounded-full bg-white/[0.06] h-3 overflow-hidden">
        <motion.div
          className={`rounded-full h-3 ${color}`}
          initial={{ width: 0 }}
          animate={{ width: `${Math.min(weight * 100, 100)}%` }}
          transition={{ duration: 0.6, delay: 0.2 + index * 0.06, ease: "easeOut" }}
        />
      </div>
      <span className="w-14 text-right font-mono text-sm font-medium text-neutral-400">
        {(weight * 100).toFixed(1)}%
      </span>
    </motion.div>
  );
}

function PerformanceContent() {
  const [stats, setStats] = useState<any>(null);
  const [portfolio, setPortfolio] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [showExpert, setShowExpert] = useState(false);
  const [error, setError] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const toast = useToast();

  const loadData = useCallback(() => {
    setLoading(true);
    setError(false);
    Promise.all([
      gatewayFetch("/statistics").catch(() => null),
      gatewayFetch("/portfolio").catch(() => null),
    ]).then(([s, p]) => {
      if (!s && !p) {
        setError(true);
        toast.show("error", "성과 데이터를 불러오지 못했습니다");
      } else {
        setLastUpdated(Date.now());
      }
      setStats(s);
      setPortfolio(p);
      setLoading(false);
    });
  }, [toast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  if (loading) {
    return (
      <main className="grid gap-6">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6 animate-pulse h-40"
          />
        ))}
      </main>
    );
  }

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

  const totalReturn = stats?.total_return ?? 0;
  const totalReturnPct = totalReturn * 100;
  const winRate = stats?.win_rate ?? 0;
  const winRatePct = winRate * 100;
  const tradeCount = stats?.trade_count ?? 0;
  const maxDrawdown = stats?.max_drawdown ?? 0;
  const maxDrawdownPct = maxDrawdown * 100;

  return (
    <PageTransition>
      <main className="grid gap-6">
        {/* Header */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
          <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">
            Performance
          </p>
          <h2 className="mt-1 text-2xl font-semibold text-white">
            에이전트 성과
          </h2>
          <p className="mt-1 text-sm text-neutral-500">
            AI 에이전트의 트레이딩 성과를 한눈에 확인하세요
          </p>
          {lastUpdated && (
            <span className="text-[10px] text-zinc-600">
              마지막 업데이트: {new Date(lastUpdated).toLocaleTimeString("ko-KR")}
            </span>
          )}
        </section>

        {/* ── Hero: 총 수익률 ──────────────────────────────────── */}
        <FadeInView>
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-8 text-center">
            <p className="text-sm font-medium text-neutral-400">총 수익률</p>
            <div
              className={`mt-2 font-mono text-5xl font-bold tracking-tight ${
                totalReturn >= 0 ? "text-emerald-400" : "text-red-400"
              }`}
            >
              {totalReturn >= 0 ? "+" : ""}
              <AnimatedNumber value={totalReturnPct} decimals={2} />
              <span className="text-3xl">%</span>
            </div>
            <p className="mt-2 text-xs text-neutral-400">
              총 {tradeCount}건 거래 기준
            </p>
          </section>
        </FadeInView>

        {/* ── Key Metrics (friendly) ──────────────────────────── */}
        <FadeInView delay={0.05}>
          <StaggerContainer className="grid gap-4 sm:grid-cols-3">
            {/* 승률 */}
            <StaggerItem>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
                <p className="text-xs font-medium text-neutral-400">승률</p>
                <div className="mt-2 flex items-end gap-2">
                  <span className="font-mono text-3xl font-bold text-white">
                    <AnimatedNumber value={winRatePct} decimals={1} />
                  </span>
                  <span className="mb-1 text-lg text-neutral-400">%</span>
                </div>
                {/* visual win-rate bar */}
                <div className="mt-3 h-2 rounded-full bg-white/[0.06] overflow-hidden">
                  <motion.div
                    className="h-2 rounded-full bg-green-500"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(winRatePct, 100)}%` }}
                    transition={{ duration: 0.7, delay: 0.3, ease: "easeOut" }}
                  />
                </div>
              </div>
            </StaggerItem>

            {/* 거래 횟수 */}
            <StaggerItem>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
                <p className="text-xs font-medium text-neutral-400">거래 횟수</p>
                <div className="mt-2 flex items-end gap-2">
                  <span className="font-mono text-3xl font-bold text-white">
                    <AnimatedNumber value={tradeCount} decimals={0} />
                  </span>
                  <span className="mb-1 text-lg text-neutral-400">건</span>
                </div>
              </div>
            </StaggerItem>

            {/* 최대 하락폭 */}
            <StaggerItem>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
                <p className="text-xs font-medium text-neutral-400">최대 하락폭</p>
                <div className="mt-2 flex items-end gap-2">
                  <span className="font-mono text-3xl font-bold text-red-400">
                    <AnimatedNumber value={Math.abs(maxDrawdownPct)} decimals={1} />
                  </span>
                  <span className="mb-1 text-lg text-red-400">%</span>
                </div>
                <div className="mt-3 h-2 rounded-full bg-white/[0.06] overflow-hidden">
                  <motion.div
                    className="h-2 rounded-full bg-red-400"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.min(Math.abs(maxDrawdownPct), 100)}%` }}
                    transition={{ duration: 0.7, delay: 0.3, ease: "easeOut" }}
                  />
                </div>
              </div>
            </StaggerItem>
          </StaggerContainer>
        </FadeInView>

        {/* ── Portfolio Cards ─────────────────────────────────── */}
        <FadeInView delay={0.1}>
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">
              Portfolio
            </p>
            <h3 className="mt-1 text-lg font-semibold text-white">
              포트폴리오
            </h3>

            <StaggerContainer className="mt-5 grid gap-4 sm:grid-cols-3">
              {/* 총 투자금 */}
              <StaggerItem>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
                  <p className="text-xs font-medium text-neutral-400">총 투자금</p>
                  <p className="mt-2 font-mono text-2xl font-bold text-white">
                    $<AnimatedNumber value={portfolio?.total_exposure ?? 0} decimals={2} />
                  </p>
                </div>
              </StaggerItem>

              {/* 평가 손익 */}
              <StaggerItem>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
                  <p className="text-xs font-medium text-neutral-400">평가 손익</p>
                  <p
                    className={`mt-2 font-mono text-2xl font-bold ${
                      (portfolio?.unrealized_pnl ?? 0) >= 0
                        ? "text-emerald-400"
                        : "text-red-400"
                    }`}
                  >
                    {(portfolio?.unrealized_pnl ?? 0) >= 0 ? "+" : ""}$
                    <AnimatedNumber
                      value={portfolio?.unrealized_pnl ?? 0}
                      decimals={2}
                    />
                  </p>
                </div>
              </StaggerItem>

              {/* 실현 손익 */}
              <StaggerItem>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
                  <p className="text-xs font-medium text-neutral-400">실현 손익</p>
                  <p
                    className={`mt-2 font-mono text-2xl font-bold ${
                      (portfolio?.realized_pnl ?? 0) >= 0
                        ? "text-emerald-400"
                        : "text-red-400"
                    }`}
                  >
                    {(portfolio?.realized_pnl ?? 0) >= 0 ? "+" : ""}$
                    <AnimatedNumber
                      value={portfolio?.realized_pnl ?? 0}
                      decimals={2}
                    />
                  </p>
                </div>
              </StaggerItem>
            </StaggerContainer>

            {/* 자산 비중 */}
            {portfolio?.concentration &&
              Object.keys(portfolio.concentration).length > 0 && (
                <div className="mt-6">
                  <p className="mb-3 text-xs font-medium text-neutral-400">
                    자산 비중
                  </p>
                  <div className="space-y-3">
                    {Object.entries(portfolio.concentration).map(
                      ([asset, weight]: [string, any], idx: number) => (
                        <ConcentrationBar
                          key={asset}
                          asset={asset}
                          weight={weight}
                          index={idx}
                        />
                      )
                    )}
                  </div>
                </div>
              )}
          </section>
        </FadeInView>

        {/* ── Recent PnL Chart (animated bars) ────────────────── */}
        {stats?.recent_trade_pnls && stats.recent_trade_pnls.length > 0 && (
          <FadeInView delay={0.15}>
            <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
              <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">
                Recent PnL
              </p>
              <h3 className="mt-1 text-lg font-semibold text-white">
                최근 거래 손익
              </h3>
              <div className="mt-4 flex items-end gap-1 h-32">
                {stats.recent_trade_pnls.map((pnl: number, i: number) => {
                  const maxAbs = Math.max(
                    ...stats.recent_trade_pnls.map(Math.abs),
                    0.01
                  );
                  return (
                    <PnlBar key={i} value={pnl} maxAbs={maxAbs} index={i} />
                  );
                })}
              </div>
              <div className="mt-2 flex justify-between text-[10px] text-neutral-300">
                <span>과거</span>
                <span>최근</span>
              </div>
            </section>
          </FadeInView>
        )}

        {/* ── Expert Metrics (collapsible) ────────────────────── */}
        <FadeInView delay={0.2}>
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.03]">
            <button
              onClick={() => setShowExpert(!showExpert)}
              className="flex w-full items-center justify-between p-6 text-left"
            >
              <div>
                <p className="text-xs font-medium text-neutral-400">
                  Advanced
                </p>
                <h3 className="mt-1 text-lg font-semibold text-white">
                  전문가 지표
                </h3>
              </div>
              <motion.span
                animate={{ rotate: showExpert ? 180 : 0 }}
                transition={{ duration: 0.25 }}
                className="text-neutral-400"
              >
                <svg
                  width="20"
                  height="20"
                  viewBox="0 0 20 20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M6 8l4 4 4-4" />
                </svg>
              </motion.span>
            </button>

            <Expandable open={showExpert}>
              <div className="px-6 pb-6">
                <StaggerContainer className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    {
                      label: "샤프 비율 (Sharpe)",
                      tip: "위험 대비 수익 효율",
                      value: stats?.sharpe,
                      fmt: (v: number) => v.toFixed(2),
                    },
                    {
                      label: "소르티노 비율 (Sortino)",
                      tip: "하방 위험 대비 수익",
                      value: stats?.sortino,
                      fmt: (v: number) => v.toFixed(2),
                    },
                    {
                      label: "수익 팩터 (Profit Factor)",
                      tip: "총이익 / 총손실",
                      value: stats?.profit_factor,
                      fmt: (v: number) => v.toFixed(2),
                    },
                    {
                      label: "기대값 (Expectancy)",
                      tip: "거래당 평균 기대 수익",
                      value: stats?.expectancy,
                      fmt: (v: number) => v.toFixed(4),
                      color: (v: number) =>
                        v >= 0 ? "text-emerald-400" : "text-red-400",
                    },
                  ].map((m, i) => (
                    <StaggerItem key={i}>
                      <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-4">
                        <p className="text-xs font-medium text-neutral-400">
                          {m.label}
                        </p>
                        <p className="mt-0.5 text-[10px] text-neutral-300">
                          {m.tip}
                        </p>
                        <p
                          className={`mt-2 font-mono text-xl font-semibold ${
                            m.color
                              ? m.color(m.value ?? 0)
                              : "text-white"
                          }`}
                        >
                          {m.value != null ? m.fmt(m.value) : "--"}
                        </p>
                      </div>
                    </StaggerItem>
                  ))}
                </StaggerContainer>
              </div>
            </Expandable>
          </section>
        </FadeInView>
      </main>
    </PageTransition>
  );
}

export default function PerformancePage() {
  return (
    <AuthGuard>
      <PerformanceContent />
    </AuthGuard>
  );
}
