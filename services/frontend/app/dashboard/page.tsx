"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  motion,
} from "../../components/motion";

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

function formatNum(v: number | undefined | null, d = 2): string {
  if (v == null) return "--";
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

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

  if (loading) {
    return (
      <main className="grid gap-6">
        {[0,1,2].map(i => (
          <div key={i} className="rounded border border-neutral-200 bg-white p-6 animate-pulse space-y-3">
            <div className="skeleton h-6 w-48" />
            <div className="skeleton h-32 w-full" />
          </div>
        ))}
      </main>
    );
  }

  const portfolio = data?.portfolio;
  const stats = data?.statistics;
  const posCount = portfolio?.positions ? Object.keys(portfolio.positions).length : 0;
  const topRec = recs[0];

  return (
    <PageTransition>
      <main className="grid gap-6">
        {/* Agent Status Banner */}
        <section className="rounded border border-neutral-200 bg-white p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">OVERVIEW</p>
              <h2 className="mt-1 text-2xl font-semibold text-neutral-900">에이전트 대시보드</h2>
              <p className="mt-1 text-sm text-neutral-500">
                AI 에이전트가 시장을 분석하고 자동으로 의사결정합니다
              </p>
            </div>
            <div className="flex items-center gap-3">
              <span className="live-dot" />
              <span className="text-sm text-neutral-500">에이전트 활성</span>
            </div>
          </div>
        </section>

        {/* Row 1: Agent Recommendation + Portfolio */}
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Agent's Current Recommendation */}
          <FadeInView>
            <section className="rounded border border-neutral-200 bg-white p-6">
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">RECOMMENDATION</p>
              <h3 className="mt-2 text-lg font-semibold text-neutral-900">
                에이전트 추천 전략
              </h3>
              {topRec ? (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xl font-bold text-neutral-900">{topRec.name}</span>
                    <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-600">
                      신뢰도 {(topRec.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="text-sm text-neutral-600">{topRec.reasoning}</p>
                  <div className="flex flex-wrap gap-2">
                    <span className="inline-flex items-center rounded-full bg-neutral-900 px-2 py-0.5 text-xs font-medium text-white">{topRec.formula_name}</span>
                    <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-500">{topRec.regime}</span>
                  </div>
                  {recs.length > 1 && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-neutral-400 hover:text-neutral-700">
                        다른 추천 {recs.length - 1}개 보기
                      </summary>
                      <div className="mt-2 space-y-2">
                        {recs.slice(1).map((r, i) => (
                          <div key={i} className="rounded border border-neutral-200 bg-white p-3">
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-medium text-neutral-900">{r.name}</span>
                              <span className="font-mono text-xs text-neutral-400">{(r.confidence * 100).toFixed(0)}%</span>
                            </div>
                            <p className="mt-1 text-xs text-neutral-500">{r.reasoning}</p>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              ) : (
                <p className="mt-4 text-sm text-neutral-400">에이전트가 시장을 분석 중입니다...</p>
              )}
            </section>
          </FadeInView>

          {/* Portfolio Summary */}
          <FadeInView delay={0.1}>
            <section className="rounded border border-neutral-200 bg-white p-6">
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">PORTFOLIO</p>
              <h3 className="mt-2 text-lg font-semibold text-neutral-900">포트폴리오</h3>
              <StaggerContainer className="mt-4 grid grid-cols-2 gap-3">
                <StaggerItem>
                  <div className="rounded border border-neutral-200 bg-white p-3">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">총 노출</p>
                    <p className="mt-1 font-mono text-2xl font-semibold text-neutral-900">
                      ${formatNum(portfolio?.total_exposure, 0)}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded border border-neutral-200 bg-white p-3">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">미실현 PnL</p>
                    <p className={`mt-1 font-mono text-2xl font-semibold ${(portfolio?.unrealized_pnl ?? 0) >= 0 ? "text-green-600" : "text-red-600"}`}>
                      {(portfolio?.unrealized_pnl ?? 0) >= 0 ? "+" : ""}${formatNum(portfolio?.unrealized_pnl)}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded border border-neutral-200 bg-white p-3">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">거래 수</p>
                    <p className="mt-1 font-mono text-2xl font-semibold text-neutral-900">
                      {stats?.trade_count ?? "--"}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded border border-neutral-200 bg-white p-3">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">승률</p>
                    <p className="mt-1 font-mono text-2xl font-semibold text-neutral-900">
                      {stats?.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : "--"}
                    </p>
                  </div>
                </StaggerItem>
              </StaggerContainer>
            </section>
          </FadeInView>
        </div>

        {/* Row 2: Recent Agent Decisions */}
        <FadeInView delay={0.15}>
          <section className="rounded border border-neutral-200 bg-white p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">DECISIONS</p>
            <h3 className="mt-2 text-lg font-semibold text-neutral-900">최근 에이전트 결정</h3>
            {decisions.length === 0 ? (
              <p className="mt-4 text-sm text-neutral-400">아직 의사결정 이력이 없습니다. 에이전트가 시장 데이터를 수집하면 자동으로 결정합니다.</p>
            ) : (
              <StaggerContainer className="mt-4 space-y-3">
                {decisions.map((d, i) => {
                  const isBuy = d.action === "BUY";
                  const isSell = d.action === "SELL";
                  return (
                    <StaggerItem key={d.decision_id ?? i}>
                      <div className={`rounded border border-neutral-200 p-4 ${
                        isBuy ? "border-l-2 border-l-green-600" :
                        isSell ? "border-l-2 border-l-red-600" :
                        "border-l-2 border-l-neutral-200"
                      }`}>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <span className="text-sm font-semibold text-neutral-900">{d.asset}</span>
                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                              isBuy ? "bg-neutral-900 text-white" :
                              isSell ? "border border-neutral-900 text-neutral-900" :
                              "bg-neutral-100 text-neutral-400"
                            }`}>{d.action}</span>
                            <span className="font-mono text-sm text-neutral-500">
                              {d.signal_score?.toFixed(4)}
                            </span>
                          </div>
                          {d.timestamp && (
                            <span className="text-xs text-neutral-400">
                              {new Date(d.timestamp).toLocaleString("ko-KR")}
                            </span>
                          )}
                        </div>
                        {d.reasoning && (
                          <p className="mt-2 text-xs text-neutral-500">{d.reasoning}</p>
                        )}
                      </div>
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

export default function DashboardPage() {
  return (
    <AuthGuard>
      <DashboardContent />
    </AuthGuard>
  );
}
