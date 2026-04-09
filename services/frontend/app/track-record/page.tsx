"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";

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

export default function TrackRecordPage() {
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [asset, setAsset] = useState("BTCUSDT");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = () => {
      setLoading(true);
      // Use plain fetch (no auth header) — this is a public endpoint
      fetch(`/api/gateway/track-record/${asset}`)
        .then((r) => r.json())
        .then((data) => {
          setPortfolio(data);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    };
    load();
    const interval = setInterval(load, 60000); // refresh every 60s
    return () => clearInterval(interval);
  }, [asset]);

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-16">
      <div className="mx-auto max-w-5xl">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <h1 className="text-4xl font-bold text-white text-glow">AI 트레이딩 성과</h1>
          <p className="mt-2 text-zinc-400">실시간으로 분석한 가상 매매 기록입니다. 자본 없이 검증된 성과.</p>
        </motion.div>

        {/* Asset selector */}
        <div className="mt-8 flex gap-2">
          {["BTCUSDT", "ETHUSDT", "SOLUSDT"].map((a) => (
            <button
              key={a}
              onClick={() => setAsset(a)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
                asset === a
                  ? "bg-white text-black"
                  : "border border-white/[0.06] text-zinc-400 hover:border-white/[0.10]"
              }`}
            >
              {a}
            </button>
          ))}
        </div>

        {loading && <p className="mt-12 text-center text-zinc-500">로딩 중...</p>}

        {portfolio && !loading && (
          <>
            {/* Hero stats */}
            <div className="mt-8 grid grid-cols-2 gap-4 md:grid-cols-4">
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6"
              >
                <p className="text-[10px] uppercase tracking-wider text-zinc-500">누적 수익률</p>
                <p
                  className={`mt-2 text-3xl font-bold ${
                    portfolio.cumulative_return_pct >= 0
                      ? "text-emerald-400 profit-glow"
                      : "text-red-400 loss-glow"
                  }`}
                >
                  {portfolio.cumulative_return_pct >= 0 ? "+" : ""}
                  {portfolio.cumulative_return_pct.toFixed(2)}%
                </p>
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.05 }}
                className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6"
              >
                <p className="text-[10px] uppercase tracking-wider text-zinc-500">승률</p>
                <p className="mt-2 text-3xl font-bold text-white text-glow-strong">
                  {(portfolio.win_rate * 100).toFixed(0)}%
                </p>
                <p className="mt-1 text-[10px] text-zinc-500">
                  {portfolio.win_count}승 {portfolio.loss_count}패
                </p>
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1 }}
                className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6"
              >
                <p className="text-[10px] uppercase tracking-wider text-zinc-500">총 거래</p>
                <p className="mt-2 text-3xl font-bold text-white">{portfolio.total_trades}</p>
                <p className="mt-1 text-[10px] text-zinc-500">결정 {portfolio.total_decisions}건</p>
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.15 }}
                className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6"
              >
                <p className="text-[10px] uppercase tracking-wider text-zinc-500">최대 낙폭</p>
                <p className="mt-2 text-3xl font-bold text-red-400 loss-glow">
                  -{portfolio.max_drawdown_pct.toFixed(2)}%
                </p>
              </motion.div>
            </div>

            {/* Open position */}
            {portfolio.open_position && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.2 }}
                className="mt-6 rounded-2xl border border-white/[0.10] bg-white/[0.04] p-6"
              >
                <p className="text-sm font-semibold text-white">현재 보유중</p>
                <div className="mt-3 grid grid-cols-3 gap-4">
                  <div>
                    <p className="text-[10px] text-zinc-500">진입가</p>
                    <p className="text-lg font-semibold text-white">
                      ${portfolio.open_position.entry_price.toLocaleString()}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] text-zinc-500">현재가</p>
                    <p className="text-lg font-semibold text-white">
                      ${portfolio.current_price.toLocaleString()}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] text-zinc-500">미실현 손익</p>
                    <p
                      className={`text-lg font-semibold ${
                        portfolio.open_position.unrealized_pnl_pct >= 0
                          ? "text-emerald-400"
                          : "text-red-400"
                      }`}
                    >
                      {portfolio.open_position.unrealized_pnl_pct >= 0 ? "+" : ""}
                      {portfolio.open_position.unrealized_pnl_pct.toFixed(2)}%
                    </p>
                  </div>
                </div>
              </motion.div>
            )}

            {/* Recent trades */}
            {portfolio.recent_trades.length > 0 && (
              <div className="mt-8">
                <h2 className="text-lg font-semibold text-white">최근 거래</h2>
                <div className="mt-4 space-y-2">
                  {portfolio.recent_trades
                    .slice()
                    .reverse()
                    .map((t, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] p-4"
                      >
                        <div>
                          <p className="text-xs text-zinc-500">
                            {new Date(t.entry_time).toLocaleString("ko-KR")}
                          </p>
                          <p className="text-sm text-zinc-300">
                            ${t.entry_price.toLocaleString()} → ${t.exit_price.toLocaleString()}
                          </p>
                        </div>
                        <div className="text-right">
                          <p
                            className={`text-lg font-semibold ${
                              t.win ? "text-emerald-400" : "text-red-400"
                            }`}
                          >
                            {t.pnl_pct >= 0 ? "+" : ""}
                            {t.pnl_pct.toFixed(2)}%
                          </p>
                          <p className="text-[10px] text-zinc-500">{t.duration_hours.toFixed(1)}h 보유</p>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            )}

            <p className="mt-8 text-center text-xs text-zinc-500">
              AI 에이전트가 5분마다 시장을 분석하고 있습니다
            </p>
          </>
        )}
      </div>
    </main>
  );
}
