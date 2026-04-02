"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  AnimatedNumber,
} from "../../components/motion";

function PerformanceContent() {
  const [stats, setStats] = useState<any>(null);
  const [portfolio, setPortfolio] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      gatewayFetch("/statistics").catch(() => null),
      gatewayFetch("/portfolio").catch(() => null),
    ]).then(([s, p]) => {
      setStats(s);
      setPortfolio(p);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return <main className="grid gap-6">{[0,1].map(i => <div key={i} className="card skeleton h-40" />)}</main>;
  }

  return (
    <PageTransition>
      <main className="grid gap-6">
        <section className="card">
          <h2 className="text-2xl font-semibold text-neutral-900">에이전트 성과</h2>
          <p className="mt-1 text-sm text-neutral-500">AI 에이전트의 트레이딩 성과를 추적합니다</p>
        </section>

        {/* Key Metrics */}
        <FadeInView>
          <StaggerContainer className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: "총 수익률", value: stats?.total_return, fmt: (v: number) => `${(v * 100).toFixed(2)}%`, color: (v: number) => v >= 0 ? "text-green-600" : "text-red-600" },
              { label: "샤프 비율", value: stats?.sharpe, fmt: (v: number) => v.toFixed(2) },
              { label: "승률", value: stats?.win_rate, fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
              { label: "Profit Factor", value: stats?.profit_factor, fmt: (v: number) => v.toFixed(2) },
              { label: "소르티노 비율", value: stats?.sortino, fmt: (v: number) => v.toFixed(2) },
              { label: "최대 낙폭", value: stats?.max_drawdown, fmt: (v: number) => `${(v * 100).toFixed(1)}%`, color: () => "text-red-600" },
              { label: "총 거래 수", value: stats?.trade_count, fmt: (v: number) => String(v) },
              { label: "기대 수익", value: stats?.expectancy, fmt: (v: number) => v.toFixed(4), color: (v: number) => v >= 0 ? "text-green-600" : "text-red-600" },
            ].map((m, i) => (
              <StaggerItem key={i}>
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                  <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">{m.label}</p>
                  <p className={`mt-1 text-2xl font-semibold ${m.color ? m.color(m.value ?? 0) : "text-neutral-900"}`}>
                    {m.value != null ? m.fmt(m.value) : "--"}
                  </p>
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
        </FadeInView>

        {/* Portfolio Details */}
        <FadeInView delay={0.1}>
          <section className="card">
            <h3 className="mb-4 text-lg font-semibold text-neutral-900">포트폴리오 상세</h3>
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                <p className="text-xs text-neutral-400">총 노출</p>
                <p className="mt-1 text-xl font-semibold text-neutral-900">${portfolio?.total_exposure?.toLocaleString() ?? "0"}</p>
              </div>
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                <p className="text-xs text-neutral-400">미실현 PnL</p>
                <p className={`mt-1 text-xl font-semibold ${(portfolio?.unrealized_pnl ?? 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
                  ${portfolio?.unrealized_pnl?.toFixed(2) ?? "0.00"}
                </p>
              </div>
              <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                <p className="text-xs text-neutral-400">실현 PnL</p>
                <p className={`mt-1 text-xl font-semibold ${(portfolio?.realized_pnl ?? 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
                  ${portfolio?.realized_pnl?.toFixed(2) ?? "0.00"}
                </p>
              </div>
            </div>
            {portfolio?.concentration && Object.keys(portfolio.concentration).length > 0 && (
              <div className="mt-4">
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-neutral-400">자산 집중도</p>
                <div className="space-y-2">
                  {Object.entries(portfolio.concentration).map(([asset, weight]: [string, any]) => (
                    <div key={asset} className="flex items-center gap-3">
                      <span className="w-20 text-sm text-neutral-600">{asset}</span>
                      <div className="flex-1 rounded-full bg-neutral-100 h-2">
                        <div className="rounded-full bg-neutral-900 h-2" style={{ width: `${Math.min(weight * 100, 100)}%` }} />
                      </div>
                      <span className="text-xs text-neutral-400">{(weight * 100).toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </FadeInView>

        {/* Recent Trades */}
        {stats?.recent_trade_pnls && stats.recent_trade_pnls.length > 0 && (
          <FadeInView delay={0.15}>
            <section className="card">
              <h3 className="mb-4 text-lg font-semibold text-neutral-900">최근 거래 PnL</h3>
              <div className="flex items-end gap-1 h-24">
                {stats.recent_trade_pnls.map((pnl: number, i: number) => {
                  const maxAbs = Math.max(...stats.recent_trade_pnls.map(Math.abs), 0.01);
                  const height = Math.abs(pnl / maxAbs) * 100;
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
                      <div
                        className={`w-full rounded-t ${pnl >= 0 ? "bg-green-400" : "bg-red-400"}`}
                        style={{ height: `${Math.max(height, 4)}%` }}
                      />
                    </div>
                  );
                })}
              </div>
            </section>
          </FadeInView>
        )}
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
