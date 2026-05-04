"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface PaperPortfolio {
  asset: string;
  current_price: number;
  cumulative_return_pct: number;
  total_decisions: number;
  total_trades: number;
  win_rate: number;
  win_count: number;
  loss_count: number;
  avg_pnl_pct: number;
  max_drawdown_pct: number;
  current_equity: number;
  initial_capital: number;
  open_position: any;
  recent_trades: any[];
  equity_curve: number[];
}

interface RobustnessMetric {
  mean: number;
  median: number;
  ci_lower: number;
  ci_upper: number;
}

interface Robustness {
  simulations?: number;
  confidence_level?: number;
  sharpe?: RobustnessMetric;
  max_drawdown?: RobustnessMetric;
  total_return?: RobustnessMetric;
  win_rate?: RobustnessMetric;
  profit_factor?: RobustnessMetric;
  robust?: boolean;
  trades?: number;
  min_required?: number;
  message?: string;
}

const ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

export default function TrackRecordPage() {
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [robustness, setRobustness] = useState<Robustness | null>(null);
  const [asset, setAsset] = useState("BTCUSDT");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = () => {
      setLoading(true);
      fetch(`/api/gateway/track-record/${asset}`)
        .then((r) => r.json())
        .then((data) => {
          setPortfolio(data);
          setLoading(false);
        })
        .catch(() => setLoading(false));
      fetch(`/api/gateway/track-record/${asset}/robustness`)
        .then((r) => r.json())
        .then((data) => setRobustness(data))
        .catch(() => setRobustness(null));
    };
    load();
    const interval = setInterval(load, 60000);
    return () => clearInterval(interval);
  }, [asset]);

  return (
    <div className="relative min-h-screen bg-ink overflow-hidden">
      {/* Ambient lights */}
      <div className="pointer-events-none fixed inset-0">
        <div className="bg-orb-light" style={{ top: "-100px", left: "-80px", width: "min(500px, 80vw)", height: "min(500px, 80vw)" }} />
        <div className="bg-orb-dim" style={{ bottom: "-60px", right: "-40px", width: "min(360px, 70vw)", height: "min(360px, 70vw)" }} />
        <div className="absolute inset-0 opacity-[0.06]" style={{
          backgroundImage: "linear-gradient(rgba(251,189,46,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(251,189,46,0.4) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
        }} />
      </div>

      <main className="relative z-10 mx-auto max-w-5xl px-6 py-20">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mb-10"
        >
          <div className="flex items-baseline gap-3 mb-3">
            <span className="amber-led-static" aria-hidden />
            <p className="label-eyebrow-amber">SECTION // TRACK_RECORD</p>
          </div>
          <h1 className="font-mono text-3xl sm:text-4xl font-bold tracking-tight text-paper uppercase">
            AI 트레이딩 성과
          </h1>
          <p className="mt-3 font-prose text-sm text-paper-dim leading-relaxed">
            실시간 가상 매매 기록 — 자본 없이 검증된 성과 (60s refresh)
          </p>
        </motion.div>

        {/* Asset selector */}
        <div className="flex flex-wrap gap-1 mb-8 border-b border-rule-loud pb-3">
          {ASSETS.map((a, i) => {
            const active = asset === a;
            return (
              <button
                key={a}
                onClick={() => setAsset(a)}
                className={`relative flex items-baseline gap-2 px-4 py-2 transition-colors ${
                  active ? "text-amber" : "text-paper-dim hover:text-paper"
                }`}
              >
                <span className={`font-mono text-[9px] tracking-[0.2em] ${active ? "text-amber-deep" : "text-paper-low"}`}>
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="font-mono text-[12px] font-medium uppercase tracking-[0.14em]">{a}</span>
                {active && (
                  <span aria-hidden className="absolute -bottom-[13px] left-2 right-2 h-[2px] bg-amber" style={{ boxShadow: "0 0 12px rgba(251,189,46,0.55)" }} />
                )}
              </button>
            );
          })}
        </div>

        {loading && !portfolio && (
          <p className="mt-12 text-center label-eyebrow text-paper-dim">LOADING //...</p>
        )}

        <AnimatePresence mode="wait">
          {portfolio && (
            <motion.div
              key={asset}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.4 }}
            >
              {/* Hero stats — 4 KPI panels */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <KpiPanel
                  num="01"
                  label="누적 수익률"
                  value={`${portfolio.cumulative_return_pct >= 0 ? "+" : ""}${portfolio.cumulative_return_pct.toFixed(2)}%`}
                  positive={portfolio.cumulative_return_pct >= 0}
                  bigEmphasis
                />
                <KpiPanel
                  num="02"
                  label="승률"
                  value={`${(portfolio.win_rate * 100).toFixed(0)}%`}
                  sub={`${portfolio.win_count}W · ${portfolio.loss_count}L`}
                />
                <KpiPanel
                  num="03"
                  label="총 거래"
                  value={`${portfolio.total_trades}`}
                  sub={`결정 ${portfolio.total_decisions}건`}
                />
                <KpiPanel
                  num="04"
                  label="최대 낙폭"
                  value={`-${portfolio.max_drawdown_pct.toFixed(2)}%`}
                  positive={false}
                />
              </div>

              {/* Open position */}
              {portfolio.open_position && (
                <div className="mt-6 bg-ink-50 border border-amber/40 panel-amber-tab p-6">
                  <div className="flex items-baseline gap-3 mb-4">
                    <span className="amber-led" aria-hidden />
                    <p className="label-eyebrow-amber">POSITION // OPEN</p>
                  </div>
                  <div className="grid grid-cols-3 gap-4">
                    <Field label="진입가" value={`$${portfolio.open_position.entry_price.toLocaleString()}`} />
                    <Field label="현재가" value={`$${portfolio.current_price.toLocaleString()}`} />
                    <Field
                      label="미실현 손익"
                      value={`${portfolio.open_position.unrealized_pnl_pct >= 0 ? "+" : ""}${portfolio.open_position.unrealized_pnl_pct.toFixed(2)}%`}
                      positive={portfolio.open_position.unrealized_pnl_pct >= 0}
                    />
                  </div>
                </div>
              )}

              {/* Recent trades */}
              {portfolio.recent_trades.length > 0 && (
                <section className="mt-10">
                  <div className="flex items-baseline gap-3 mb-4 pb-3 border-b border-rule-loud">
                    <p className="label-eyebrow-amber">LOG // RECENT_TRADES</p>
                    <p className="label-eyebrow tabular ml-auto">N={portfolio.recent_trades.length}</p>
                  </div>
                  <div className="space-y-1">
                    {portfolio.recent_trades.slice().reverse().map((t, i) => (
                      <div
                        key={i}
                        className="grid grid-cols-[auto_1fr_auto_auto] gap-4 items-baseline px-4 py-3 border-b border-rule hover:bg-ink-50 transition-colors"
                      >
                        <span className="font-mono text-[10px] tabular text-paper-low w-12">{String(i + 1).padStart(3, "0")}</span>
                        <div>
                          <p className="font-mono text-[11px] text-paper-dim tabular">
                            {new Date(t.entry_time).toLocaleString("ko-KR")}
                          </p>
                          <p className="font-mono text-xs text-paper-mute tabular mt-0.5">
                            ${t.entry_price.toLocaleString()} → ${t.exit_price.toLocaleString()}
                          </p>
                        </div>
                        <span className="font-mono text-[10px] uppercase tracking-[0.12em] tabular text-paper-low">
                          {t.duration_hours.toFixed(1)}H
                        </span>
                        <span className={`font-mono text-base font-bold tabular ${
                          t.win ? "text-mint" : "text-coral"
                        }`}>
                          {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Robustness */}
              {robustness && (
                <section className="mt-12 bg-ink-50 border border-rule-loud p-6">
                  <div className="flex items-start justify-between gap-4 mb-5">
                    <div>
                      <p className="label-eyebrow-amber mb-1">VERIFIED // CONFIDENCE_INTERVAL</p>
                      <p className="font-prose text-xs text-paper-mute leading-relaxed">
                        Monte Carlo 1,000회 부트스트랩 (95% CI)
                      </p>
                    </div>
                    {robustness.robust !== undefined && robustness.sharpe && (
                      <span className={`font-mono text-[10px] tracking-[0.16em] uppercase px-2.5 py-1 border ${
                        robustness.robust
                          ? "border-mint/50 bg-mint/[0.06] text-mint"
                          : "border-rule bg-ink text-paper-mute"
                      }`}>
                        {robustness.robust ? "VERIFIED" : "BUILDING"}
                      </span>
                    )}
                  </div>

                  {robustness.message && (
                    <p className="font-prose text-sm text-paper-dim mb-4">{robustness.message}</p>
                  )}

                  {robustness.sharpe && (
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3 mb-5">
                      <RobustField
                        label="Sharpe Ratio"
                        value={robustness.sharpe.median.toFixed(2)}
                        ci={`[${robustness.sharpe.ci_lower.toFixed(2)}, ${robustness.sharpe.ci_upper.toFixed(2)}]`}
                      />
                      {robustness.max_drawdown && (
                        <RobustField
                          label="예상 최대 낙폭"
                          value={`-${(robustness.max_drawdown.median * 100).toFixed(1)}%`}
                          ci={`worst 5%: -${(robustness.max_drawdown.ci_upper * 100).toFixed(1)}%`}
                        />
                      )}
                      {robustness.total_return && (
                        <RobustField
                          label="총 수익률 분포"
                          value={`${robustness.total_return.median >= 0 ? "+" : ""}${robustness.total_return.median.toFixed(1)}%`}
                          ci={`[${robustness.total_return.ci_lower.toFixed(1)}%, ${robustness.total_return.ci_upper.toFixed(1)}%]`}
                        />
                      )}
                    </div>
                  )}

                  <p className="font-prose text-[11px] leading-relaxed text-paper-mute pt-4 border-t border-rule">
                    단일 백테스트 결과를 믿지 마세요. 위 신뢰구간은 실제 거래 기록을 1,000회 재추출하여 계산한 분포의 5–95 백분위입니다. 좁은 구간일수록 결과가 안정적이라는 의미입니다.
                  </p>
                </section>
              )}

              <p className="mt-8 text-center label-eyebrow text-paper-low">
                AGENT_CYCLE // 5MIN
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}

function KpiPanel({
  num,
  label,
  value,
  sub,
  positive,
  bigEmphasis,
}: {
  num: string;
  label: string;
  value: string;
  sub?: string;
  positive?: boolean;
  bigEmphasis?: boolean;
}) {
  const colorCls =
    positive === true ? "text-mint" : positive === false ? "text-coral" : "text-paper";
  return (
    <div className="bg-ink-50 border border-rule-loud p-5">
      <div className="flex items-baseline justify-between mb-3">
        <p className="label-eyebrow text-paper-mute">{label}</p>
        <p className="label-eyebrow tabular text-paper-low">{num}</p>
      </div>
      <p className={`font-mono ${bigEmphasis ? "text-3xl" : "text-2xl"} font-bold tabular tracking-tight ${colorCls}`}>
        {value}
      </p>
      {sub && <p className="mt-1 font-mono text-[10px] text-paper-mute tabular">{sub}</p>}
    </div>
  );
}

function Field({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  const colorCls =
    positive === true ? "text-mint" : positive === false ? "text-coral" : "text-paper";
  return (
    <div>
      <p className="label-eyebrow text-paper-mute mb-1.5">{label}</p>
      <p className={`font-mono text-lg font-semibold tabular ${colorCls}`}>{value}</p>
    </div>
  );
}

function RobustField({ label, value, ci }: { label: string; value: string; ci: string }) {
  return (
    <div className="border border-rule bg-ink p-4">
      <p className="label-eyebrow text-paper-mute mb-2">{label}</p>
      <p className="font-mono text-2xl font-bold text-paper tabular tracking-tight">{value}</p>
      <p className="mt-1.5 font-mono text-[10px] text-paper-low tabular">{ci}</p>
    </div>
  );
}
