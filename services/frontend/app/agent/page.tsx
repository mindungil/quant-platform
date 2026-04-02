"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  motion,
} from "../../components/motion";

interface Decision {
  decision_id?: string;
  asset: string;
  action: string;
  signal_score: number;
  reasoning?: string;
  timestamp?: string;
  threshold_crossed?: boolean;
  strategy_name?: string;
  components?: Record<string, number>;
  decision_phases?: Array<{
    name: string;
    status: string;
    detail?: string;
    duration_ms?: number;
  }>;
}

interface Recommendation {
  name: string;
  formula_name: string;
  regime: string;
  confidence: number;
  reasoning: string;
  indicators: string[];
  thresholds: { entry: number; exit: number };
}

const ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

function AgentContent() {
  const [selectedAsset, setSelectedAsset] = useState("BTCUSDT");
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      gatewayFetch(`/decisions/history/${selectedAsset}`).catch(() => []),
      gatewayFetch(`/recommendations/${selectedAsset}`).catch(() => []),
    ]).then(([dec, rec]) => {
      const decArr = Array.isArray(dec) ? dec : [];
      setDecisions(decArr.slice(-20).reverse());
      setRecs(Array.isArray(rec) ? rec : []);
      setLoading(false);
    });
  }, [selectedAsset]);

  useEffect(() => { load(); }, [load]);

  async function runAgent() {
    setRunning(true);
    try {
      const result = await gatewayFetch(`/decisions/run/${selectedAsset}`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      // Reload after decision
      load();
    } catch (e) {
      alert(e instanceof Error ? e.message : "에이전트 실행 실패");
    } finally {
      setRunning(false);
    }
  }

  return (
    <PageTransition>
      <main className="grid gap-6">
        {/* Header */}
        <section className="rounded border border-neutral-200 bg-white p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">AGENT</p>
              <h2 className="mt-1 text-2xl font-semibold text-neutral-900">에이전트 활동</h2>
              <p className="mt-1 text-sm text-neutral-500">
                AI 에이전트의 의사결정 과정과 이력을 실시간으로 모니터링합니다
              </p>
            </div>
            <div className="flex items-center gap-2">
              {ASSETS.map(a => (
                <button
                  key={a}
                  onClick={() => setSelectedAsset(a)}
                  className={`rounded px-3 py-1.5 text-sm font-medium transition ${
                    selectedAsset === a
                      ? "bg-neutral-900 text-white"
                      : "bg-transparent text-neutral-400 border border-neutral-200 hover:border-neutral-300"
                  }`}
                >
                  {a.replace("USDT", "")}
                </button>
              ))}
              <button
                onClick={runAgent}
                disabled={running}
                className="btn-primary ml-2 disabled:opacity-50"
              >
                {running ? "분석 중..." : "수동 실행"}
              </button>
            </div>
          </div>
        </section>

        {/* Agent Recommendations */}
        <FadeInView>
          <section className="rounded border border-neutral-200 bg-white p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">ANALYSIS</p>
            <h3 className="mt-2 text-lg font-semibold text-neutral-900">
              {selectedAsset} 시장 분석 결과
            </h3>
            {recs.length > 0 ? (
              <StaggerContainer className="mt-4 grid gap-3 md:grid-cols-3">
                {recs.map((r, i) => (
                  <StaggerItem key={i}>
                    <div className={`rounded border p-4 ${i === 0 ? "border-neutral-900" : "border-neutral-200"}`}>
                      {i === 0 && <p className="mb-2 text-xs font-medium uppercase tracking-wider text-neutral-900">최적 추천</p>}
                      <p className="text-lg font-bold text-neutral-900">{r.name}</p>
                      <p className="mt-1 text-sm text-neutral-500">{r.reasoning}</p>
                      <div className="mt-3 flex flex-wrap gap-1">
                        <span className="inline-flex items-center rounded-full bg-neutral-900 px-2 py-0.5 text-xs font-medium text-white">{r.formula_name}</span>
                        <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-600">
                          신뢰도 {(r.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            ) : (
              <p className="mt-4 text-sm text-neutral-400">시장 데이터를 수집 중입니다...</p>
            )}
          </section>
        </FadeInView>

        {/* Decision History */}
        <FadeInView delay={0.1}>
          <section className="rounded border border-neutral-200 bg-white p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">HISTORY</p>
            <h3 className="mt-2 text-lg font-semibold text-neutral-900">의사결정 이력</h3>
            {loading ? (
              <div className="mt-4 space-y-3">
                {[0,1,2].map(i => <div key={i} className="skeleton h-20 w-full" />)}
              </div>
            ) : decisions.length === 0 ? (
              <p className="mt-4 text-sm text-neutral-400">
                아직 이력이 없습니다. 에이전트가 시장 데이터를 수집하면 자동으로 의사결정을 시작합니다.
              </p>
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
                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                              isBuy ? "bg-neutral-900 text-white" :
                              isSell ? "border border-neutral-900 text-neutral-900" :
                              "bg-neutral-100 text-neutral-400"
                            }`}>{d.action}</span>
                            <span className="font-mono text-sm font-medium text-neutral-900">
                              {d.signal_score?.toFixed(4)}
                            </span>
                            {d.threshold_crossed && (
                              <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-600">임계값 돌파</span>
                            )}
                          </div>
                          {d.timestamp && (
                            <span className="text-xs text-neutral-400">
                              {new Date(d.timestamp).toLocaleString("ko-KR")}
                            </span>
                          )}
                        </div>
                        {d.reasoning && (
                          <p className="mt-2 text-xs text-neutral-500 leading-relaxed">{d.reasoning}</p>
                        )}
                        {d.decision_phases && d.decision_phases.length > 0 && (
                          <details className="mt-2">
                            <summary className="cursor-pointer text-xs text-neutral-400 hover:text-neutral-700">
                              처리 단계 ({d.decision_phases.length}개)
                            </summary>
                            <div className="mt-2 space-y-1">
                              {d.decision_phases.map((p, pi) => (
                                <div key={pi} className="flex items-center gap-2 font-mono text-xs">
                                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${p.status === "completed" ? "bg-neutral-900" : "bg-neutral-300"}`} />
                                  <span className="font-medium text-neutral-600">{p.name}</span>
                                  {p.duration_ms != null && (
                                    <span className="text-neutral-400">{p.duration_ms.toFixed(0)}ms</span>
                                  )}
                                  {p.detail && (
                                    <span className="text-neutral-400">— {p.detail}</span>
                                  )}
                                </div>
                              ))}
                            </div>
                          </details>
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

export default function AgentPage() {
  return (
    <AuthGuard>
      <AgentContent />
    </AuthGuard>
  );
}
