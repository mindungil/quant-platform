"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { useToast } from "../../components/toast";
import { ConfirmDialog } from "../../components/confirm-dialog";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  FadeInView,
  AnimatePresence,
  motion,
} from "../../components/motion";

/* ── Types ─────────────────────────────────────────────────────── */

interface Decision {
  decision_id?: string;
  asset: string;
  action: string;
  signal_score: number;
  reasoning?: string;
  timestamp?: string;
  threshold_crossed?: boolean;
  strategy_name?: string;
  formula_name?: string;
  confidence?: number;
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

/* ── Asset config ──────────────────────────────────────────────── */

const ASSET_META: Record<string, { label: string; short: string; color: string; gradient: string }> = {
  BTCUSDT: {
    label: "비트코인 (BTC)",
    short: "BTC",
    color: "#fafafa",
    gradient: "from-white to-zinc-400",
  },
  ETHUSDT: {
    label: "이더리움 (ETH)",
    short: "ETH",
    color: "#a1a1aa",
    gradient: "from-zinc-300 to-zinc-500",
  },
  SOLUSDT: {
    label: "솔라나 (SOL)",
    short: "SOL",
    color: "#71717a",
    gradient: "from-zinc-400 to-zinc-600",
  },
};

const ASSETS = Object.keys(ASSET_META);

/* ── Helpers ───────────────────────────────────────────────────── */

function actionLabel(action: string): { text: string; color: string; bg: string; ring: string } {
  switch (action) {
    case "BUY":
      return { text: "매수 추천", color: "text-emerald-700", bg: "bg-emerald-50", ring: "ring-emerald-200" };
    case "SELL":
      return { text: "매도 추천", color: "text-rose-700", bg: "bg-rose-50", ring: "ring-rose-200" };
    default:
      return { text: "관망 추천", color: "text-neutral-400", bg: "bg-white/[0.02]", ring: "ring-white/[0.06]" };
  }
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "방금 전";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.floor(hr / 24);
  return `${day}일 전`;
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
          <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-400">상승 지표</p>
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
          <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-400">하락 지표</p>
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
        <p className="text-[10px] text-zinc-500">
          과거 유사 상황 {data.memory_refs}건 참조
        </p>
      )}
    </div>
  );
}

/* ── Animated bar component ────────────────────────────────────── */

function AnimatedBar({
  value,
  max = 1,
  colorClass,
  height = "h-2",
}: {
  value: number;
  max?: number;
  colorClass: string;
  height?: string;
}) {
  const pct = Math.min(Math.max((value / max) * 100, 0), 100);
  return (
    <div className={`w-full rounded-full bg-white/[0.06] overflow-hidden ${height}`}>
      <motion.div
        className={`${height} rounded-full ${colorClass}`}
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.8, ease: "easeOut", delay: 0.2 }}
      />
    </div>
  );
}

/* ── Coin icon (colored circle with letter) ────────────────────── */

function CoinIcon({ asset, size = 32 }: { asset: string; size?: number }) {
  const meta = ASSET_META[asset];
  if (!meta) return null;
  return (
    <div
      className={`inline-flex items-center justify-center rounded-full bg-gradient-to-br ${meta.gradient} text-white font-bold shrink-0`}
      style={{ width: size, height: size, fontSize: size * 0.38 }}
    >
      {meta.short.charAt(0)}
    </div>
  );
}

/* ── Main content ──────────────────────────────────────────────── */

function AgentContent() {
  const [selectedAsset, setSelectedAsset] = useState("BTCUSDT");
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [showRunConfirm, setShowRunConfirm] = useState(false);
  const toast = useToast();

  const meta = ASSET_META[selectedAsset];

  const load = useCallback(() => {
    setLoading(true);
    setError(false);
    Promise.all([
      gatewayFetch(`/decisions/history/${selectedAsset}`).catch(() => []),
      gatewayFetch(`/recommendations/${selectedAsset}`).catch(() => []),
    ]).then(([dec, rec]) => {
      const decArr = Array.isArray(dec) ? dec : [];
      setDecisions(decArr.slice(-20).reverse());
      setRecs(Array.isArray(rec) ? rec : []);
      setLastUpdated(Date.now());
      setLoading(false);
    }).catch(() => {
      setError(true);
      setLoading(false);
      toast.show("error", "데이터를 불러오지 못했습니다");
    });
  }, [selectedAsset, toast]);

  useEffect(() => {
    load();
  }, [load]);

  async function runAgent() {
    setRunning(true);
    try {
      await gatewayFetch(`/decisions/run/${selectedAsset}`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast.show("success", "에이전트 분석이 완료되었습니다");
      load();
    } catch (e) {
      toast.show("error", e instanceof Error ? e.message : "에이전트 실행 실패");
    } finally {
      setRunning(false);
    }
  }

  /* latest decision for hero card */
  const latest = decisions[0] ?? null;
  const latestAction = latest ? actionLabel(latest.action) : null;

  if (error && !loading) {
    return (
      <PageTransition>
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="text-sm text-zinc-500">데이터를 불러오는 중 오류가 발생했습니다</p>
          <button onClick={() => { setError(false); load(); }} className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black">
            다시 시도
          </button>
        </div>
      </PageTransition>
    );
  }

  return (
    <PageTransition>
      <ConfirmDialog
        open={showRunConfirm}
        title="분석 실행"
        message={`${meta.label}에 대한 AI 분석을 실행하시겠습니까?`}
        confirmText="분석 실행"
        onConfirm={() => {
          setShowRunConfirm(false);
          runAgent();
        }}
        onCancel={() => setShowRunConfirm(false)}
      />
      <main className="grid gap-6">
        {/* ── Header ──────────────────────────────────────────── */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-2xl font-bold text-white text-glow">AI 에이전트</h2>
              <p className="mt-1 text-sm text-neutral-500">
                실시간 시장 분석과 AI 추천을 확인하세요
              </p>
              {lastUpdated && (
                <span className="text-[10px] text-zinc-500">
                  마지막 업데이트: {new Date(lastUpdated).toLocaleTimeString("ko-KR")}
                </span>
              )}
            </div>

            {/* Asset tabs */}
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 rounded-xl bg-white/[0.06] p-1">
                {ASSETS.map((a) => {
                  const am = ASSET_META[a];
                  const active = selectedAsset === a;
                  return (
                    <button
                      key={a}
                      onClick={() => setSelectedAsset(a)}
                      className={`relative flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-all duration-200 ${
                        active
                          ? "bg-white/[0.10] text-white"
                          : "text-neutral-500 hover:text-neutral-300"
                      }`}
                    >
                      <CoinIcon asset={a} size={22} />
                      <span className="hidden sm:inline">{am.short}</span>
                    </button>
                  );
                })}
              </div>

              <button
                onClick={() => setShowRunConfirm(true)}
                disabled={running}
                className="ml-2 flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-black transition-all hover:shadow-[0_0_20px_rgba(255,255,255,0.15)] active:scale-[0.97] disabled:opacity-50"
              >
                {running ? (
                  <>
                    <motion.span
                      className="inline-block h-4 w-4 rounded-full border-2 border-black border-t-transparent"
                      animate={{ rotate: 360 }}
                      transition={{ repeat: Infinity, duration: 0.7, ease: "linear" }}
                    />
                    분석 중...
                  </>
                ) : (
                  "분석 실행"
                )}
              </button>
            </div>
          </div>
        </section>

        {/* ── AI Recommendations ──────────────────────────────── */}
        <AnimatePresence mode="wait">
          <motion.div
            key={selectedAsset + "-recs"}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.25 }}
          >
            <section>
              <div className="mb-4 flex items-center gap-2">
                <CoinIcon asset={selectedAsset} size={28} />
                <h3 className="text-lg font-bold text-white">
                  {meta.label} AI 추천
                </h3>
              </div>

              {recs.length > 0 ? (
                <StaggerContainer className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {recs.map((r, i) => (
                    <StaggerItem key={i}>
                      <motion.div
                        className={`group relative rounded-xl border bg-white/[0.03] p-5 transition-all duration-200 hover-lift ${
                          i === 0
                            ? "border-white/[0.20] ring-1 ring-white/[0.06]"
                            : "border-white/[0.06] hover:border-white/[0.10]"
                        }`}
                        whileHover={{ scale: 1.015, y: -2 }}
                        transition={{ type: "spring", stiffness: 400, damping: 25 }}
                      >
                        {i === 0 && (
                          <span className="absolute -top-2.5 left-4 inline-flex items-center rounded-full bg-white px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-black">
                            최적 추천
                          </span>
                        )}

                        <p className="mt-1 text-base font-bold text-white">{r.name}</p>

                        <p className="mt-2.5 text-sm leading-relaxed text-neutral-400">
                          {r.reasoning}
                        </p>

                        {/* Confidence bar */}
                        <div className="mt-4">
                          <div className="mb-1.5 flex items-center justify-between">
                            <span className="text-xs font-medium text-neutral-500">신뢰도</span>
                            <span className="text-xs font-bold text-neutral-300 text-glow">
                              {(r.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                          <AnimatedBar
                            value={r.confidence}
                            colorClass={`bg-gradient-to-r ${meta.gradient}`}
                          />
                        </div>

                        <p className="mt-3 text-[11px] text-neutral-400">
                          분석 방식: {r.formula_name}
                        </p>
                      </motion.div>
                    </StaggerItem>
                  ))}
                </StaggerContainer>
              ) : (
                <div className="flex items-center justify-center rounded-xl border border-dashed border-white/[0.06] bg-white/[0.02] py-12">
                  <div className="text-center">
                    <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-white/[0.06]">
                      <CoinIcon asset={selectedAsset} size={24} />
                    </div>
                    <p className="text-sm text-neutral-400">
                      시장 데이터를 수집 중입니다...
                    </p>
                  </div>
                </div>
              )}
            </section>
          </motion.div>
        </AnimatePresence>

        {/* ── Decision History (Timeline) ─────────────────────── */}
        <AnimatePresence mode="wait">
          <motion.div
            key={selectedAsset + "-hist"}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.25, delay: 0.05 }}
          >
            <FadeInView delay={0.1}>
              <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
                <h3 className="text-lg font-bold text-white">최근 판단</h3>
                <p className="mt-1 text-sm text-neutral-400">
                  AI 에이전트의 최근 분석 기록
                </p>

                {loading ? (
                  <div className="mt-6 space-y-4">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="flex gap-4">
                        <div className="skeleton h-10 w-10 shrink-0 rounded-full" />
                        <div className="flex-1 space-y-2">
                          <div className="skeleton h-4 w-1/3 rounded" />
                          <div className="skeleton h-3 w-full rounded" />
                          <div className="skeleton h-3 w-2/3 rounded" />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : decisions.length === 0 ? (
                  <div className="mt-8 flex flex-col items-center py-8 text-center">
                    <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.06] text-xl">
                      📊
                    </div>
                    <p className="text-sm text-neutral-400">
                      아직 판단 이력이 없습니다
                    </p>
                    <p className="mt-1 text-xs text-neutral-300">
                      에이전트가 시장 데이터를 수집하면 자동으로 분석을 시작합니다
                    </p>
                  </div>
                ) : (
                  <div className="relative mt-6">
                    {/* Timeline line */}
                    <div className="absolute left-5 top-0 bottom-0 w-px bg-white/[0.06]" />

                    <StaggerContainer className="space-y-1">
                      {decisions.map((d, i) => {
                        const act = actionLabel(d.action);
                        const score = Math.abs(d.signal_score);
                        const normalizedScore = Math.min(score, 1);

                        return (
                          <StaggerItem key={d.decision_id ?? i}>
                            <motion.div
                              initial={{ opacity: 0, x: -20 }}
                              animate={{ opacity: 1, x: 0 }}
                              transition={{ duration: 0.35, delay: i * 0.04 }}
                              className="group relative flex gap-4 rounded-xl p-3 transition-colors hover:bg-white/[0.03]"
                            >
                              {/* Timeline dot */}
                              <div className="relative z-10 mt-1 shrink-0">
                                <div
                                  className={`flex h-10 w-10 items-center justify-center rounded-full ${act.bg} ring-2 ring-white/[0.06] ${act.ring} transition-shadow`}
                                >
                                  <span className="text-sm">
                                    {d.action === "BUY" ? "▲" : d.action === "SELL" ? "▼" : "●"}
                                  </span>
                                </div>
                              </div>

                              {/* Content */}
                              <div className="min-w-0 flex-1 pb-4">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span
                                    className={`inline-flex items-center rounded-lg px-2.5 py-1 text-xs font-semibold ${act.bg} ${act.color} ring-1 ${act.ring}`}
                                  >
                                    {act.text}
                                  </span>
                                  {d.timestamp && (
                                    <span className="text-xs text-neutral-400">
                                      {relativeTime(d.timestamp)}
                                    </span>
                                  )}
                                </div>

                                {/* Reasoning - structured card */}
                                {d.reasoning && (
                                  <div className="mt-2">
                                    <ReasoningCard reasoning={d.reasoning} />
                                  </div>
                                )}

                                {/* Signal strength */}
                                <div className="mt-3 max-w-xs">
                                  <div className="mb-1 flex items-center justify-between">
                                    <span className="text-[11px] font-medium text-neutral-400">
                                      신호 강도
                                    </span>
                                    <span className="text-[11px] font-bold text-neutral-500">
                                      {(normalizedScore * 100).toFixed(0)}%
                                    </span>
                                  </div>
                                  <AnimatedBar
                                    value={normalizedScore}
                                    height="h-1.5"
                                    colorClass={
                                      d.action === "BUY"
                                        ? "bg-gradient-to-r from-emerald-400 to-emerald-600"
                                        : d.action === "SELL"
                                          ? "bg-gradient-to-r from-rose-400 to-rose-600"
                                          : "bg-gradient-to-r from-neutral-300 to-neutral-400"
                                    }
                                  />
                                </div>

                                {/* Formula name if present */}
                                {(d.formula_name || d.strategy_name) && (
                                  <p className="mt-2 text-[11px] text-neutral-400">
                                    분석 방식: {d.formula_name || d.strategy_name}
                                  </p>
                                )}
                              </div>
                            </motion.div>
                          </StaggerItem>
                        );
                      })}
                    </StaggerContainer>
                  </div>
                )}
              </section>
            </FadeInView>
          </motion.div>
        </AnimatePresence>
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
