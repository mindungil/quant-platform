"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { GradientCard } from "../../components/gradient-card";
import { AnimatedStat } from "../../components/animated-stat";
import { PulseDot } from "../../components/pulse-dot";
import { Sparkline } from "../../components/sparkline";
import { AuthGuard } from "../../components/auth-guard";
import { gatewayFetch } from "../../lib/api";

// Types
interface AlphaHealth {
  name: string;
  status: "HEALTHY" | "DEGRADED" | "CRITICAL";
  sharpe_7d: number;
  sharpe_30d: number;
  sharpe_90d: number;
  weight: number;
}

interface MiningResult {
  timestamp: string;
  candidates_tested: number;
  passed: number;
  promoted: number;
  best_sharpe: number;
}

interface FeatureImportance {
  name: string;
  importance: number;
  category: string;
}

interface SystemMetrics {
  total_alphas: number;
  active_alphas: number;
  total_features: number;
  symbols?: number;
  last_refit?: string;
  last_mining?: string;
  mining_attempts?: number;
  paper_pnl: number;
  paper_dd: number;
}

const categoryColors: Record<string, string> = {
  momentum: "text-blue-400",
  derivatives: "text-purple-400",
  vol: "text-amber-400",
  mean_rev: "text-emerald-400",
  micro: "text-cyan-400",
  sentiment: "text-pink-400",
  volume: "text-orange-400",
  funding: "text-indigo-400",
};

const statusToPulse: Record<string, "active" | "warning" | "critical"> = {
  HEALTHY: "active",
  DEGRADED: "warning",
  CRITICAL: "critical",
};

export default function MonitoringPage() {
  return (
    <AuthGuard>
      <MonitoringPageInner />
    </AuthGuard>
  );
}

function MonitoringPageInner() {
  const [health, setHealth] = useState<AlphaHealth[]>([]);
  const [mining, setMining] = useState<MiningResult[]>([]);
  const [features, setFeatures] = useState<FeatureImportance[]>([]);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);

  const [healthLoading, setHealthLoading] = useState(true);
  const [miningLoading, setMiningLoading] = useState(true);
  const [featuresLoading, setFeaturesLoading] = useState(true);
  const [metricsLoading, setMetricsLoading] = useState(true);

  const [healthError, setHealthError] = useState<string | null>(null);
  const [miningError, setMiningError] = useState<string | null>(null);
  const [featuresError, setFeaturesError] = useState<string | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);

  useEffect(() => {
    // Alpha health
    gatewayFetch("/monitoring/alphas/health?asset=BTCUSDT")
      .then((data) => {
        const items = Array.isArray(data) ? data : (data as { alphas?: AlphaHealth[] }).alphas ?? [];
        setHealth(items as AlphaHealth[]);
      })
      .catch(() => setHealthError("데이터를 불러올 수 없습니다"))
      .finally(() => setHealthLoading(false));

    // Feature importance
    gatewayFetch("/monitoring/features/importance?asset=BTCUSDT&top_n=10")
      .then((data) => {
        const items = Array.isArray(data) ? data : (data as { features?: FeatureImportance[] }).features ?? [];
        setFeatures(items as FeatureImportance[]);
      })
      .catch(() => setFeaturesError("데이터를 불러올 수 없습니다"))
      .finally(() => setFeaturesLoading(false));

    // Mining history
    gatewayFetch("/monitoring/mining/history?limit=10")
      .then((data) => {
        const items = Array.isArray(data) ? data : (data as { runs?: MiningResult[] }).runs ?? [];
        setMining(items as MiningResult[]);
      })
      .catch(() => setMiningError("데이터를 불러올 수 없습니다"))
      .finally(() => setMiningLoading(false));

    // System metrics
    gatewayFetch("/monitoring/system/metrics")
      .then((data) => setMetrics(data as SystemMetrics))
      .catch(() => setMetricsError("데이터를 불러올 수 없습니다"))
      .finally(() => setMetricsLoading(false));
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-white">ML 모니터링</h1>
        <p className="text-sm text-[#a1a1a1] mt-1">알파 헬스, 피처 중요도, 마이닝 결과</p>
      </div>

      {/* System Overview */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {metricsLoading ? (
          Array.from({ length: 6 }).map((_, i) => (
            <GradientCard key={i} accent="neutral" delay={i * 0.05}>
              <div className="text-xs text-[#a1a1a1]">로딩 중...</div>
            </GradientCard>
          ))
        ) : metricsError ? (
          <div className="col-span-full rounded-xl border border-red-500/20 bg-red-500/5 p-4 text-center">
            <p className="text-sm text-red-400">{metricsError}</p>
          </div>
        ) : metrics ? (
          <>
            <GradientCard accent="green" delay={0}>
              <AnimatedStat label="활성 알파" value={metrics.active_alphas ?? 0} decimals={0} format="number" positive />
            </GradientCard>
            <GradientCard accent="blue" delay={0.05}>
              <AnimatedStat label="총 피처" value={metrics.total_features ?? 0} decimals={0} format="number" positive />
            </GradientCard>
            <GradientCard accent="neutral" delay={0.1}>
              <AnimatedStat label="심볼" value={metrics.symbols ?? 0} decimals={0} format="number" positive />
            </GradientCard>
            <GradientCard accent={(metrics.paper_pnl ?? 0) >= 0 ? "green" : "red"} delay={0.15}>
              <AnimatedStat label="페이퍼 PnL" value={metrics.paper_pnl ?? 0} decimals={2} suffix="%" positive={(metrics.paper_pnl ?? 0) >= 0} />
            </GradientCard>
            <GradientCard accent="neutral" delay={0.2}>
              <AnimatedStat label="마이닝 시도" value={metrics.mining_attempts ?? 0} decimals={0} format="number" positive />
            </GradientCard>
            <GradientCard accent="red" delay={0.25}>
              <AnimatedStat label="최대 DD" value={metrics.paper_dd ?? 0} decimals={1} suffix="%" positive={false} />
            </GradientCard>
          </>
        ) : (
          <div className="col-span-full text-center text-sm text-[#a1a1a1]">데이터가 없습니다</div>
        )}
      </div>

      {/* Alpha Health */}
      <GradientCard accent="neutral" delay={0.1}>
        <h2 className="text-sm font-medium text-[#a1a1a1] mb-4">알파 헬스 상태</h2>
        {healthLoading ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="text-xs text-[#a1a1a1]">로딩 중...</div>
            ))}
          </div>
        ) : healthError ? (
          <p className="text-sm text-red-400">{healthError}</p>
        ) : health.length === 0 ? (
          <p className="text-sm text-[#a1a1a1]">데이터가 없습니다</p>
        ) : (
          <div className="space-y-3">
            {health.map((alpha) => (
              <div key={alpha.name} className="flex items-center gap-3">
                <PulseDot status={statusToPulse[alpha.status]} size="md" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-white truncate">{alpha.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      alpha.status === "HEALTHY" ? "bg-emerald-500/10 text-emerald-400" :
                      alpha.status === "DEGRADED" ? "bg-amber-500/10 text-amber-400" :
                      "bg-red-500/10 text-red-400"
                    }`}>
                      {alpha.status}
                    </span>
                  </div>
                  <div className="flex gap-4 mt-1 text-[11px] text-[#a1a1a1]">
                    <span>7d: <span className={alpha.sharpe_7d > 0 ? "text-emerald-400" : "text-red-400"}>{alpha.sharpe_7d > 0 ? "+" : ""}{alpha.sharpe_7d.toFixed(2)}</span></span>
                    <span>30d: <span className={alpha.sharpe_30d > 0 ? "text-emerald-400" : "text-red-400"}>{alpha.sharpe_30d > 0 ? "+" : ""}{alpha.sharpe_30d.toFixed(2)}</span></span>
                    <span>90d: <span className={alpha.sharpe_90d > 0 ? "text-emerald-400" : "text-red-400"}>{alpha.sharpe_90d > 0 ? "+" : ""}{alpha.sharpe_90d.toFixed(2)}</span></span>
                    <span>가중치: <span className="text-white">{alpha.weight.toFixed(1)}</span></span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </GradientCard>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Feature Importance */}
        <GradientCard accent="blue" delay={0.15}>
          <h2 className="text-sm font-medium text-[#a1a1a1] mb-4">피처 중요도 Top 10</h2>
          {featuresLoading ? (
            <p className="text-xs text-[#a1a1a1]">로딩 중...</p>
          ) : featuresError ? (
            <p className="text-sm text-red-400">{featuresError}</p>
          ) : features.length === 0 ? (
            <p className="text-sm text-[#a1a1a1]">데이터가 없습니다</p>
          ) : (
            <div className="space-y-2">
              {features.map((feat, i) => {
                const maxImp = features[0].importance || 1;
                const pct = (feat.importance / maxImp) * 100;
                return (
                  <div key={feat.name} className="flex items-center gap-2">
                    <span className="text-[10px] text-zinc-600 w-4 text-right">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="font-mono text-xs text-[#a1a1a1] truncate">{feat.name}</span>
                        <span className={`text-[9px] ${categoryColors[feat.category] || "text-[#a1a1a1]"}`}>
                          {feat.category}
                        </span>
                      </div>
                      <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
                        <motion.div
                          className="h-full bg-blue-500/60 rounded-full"
                          initial={{ width: 0 }}
                          animate={{ width: `${pct}%` }}
                          transition={{ duration: 0.6, delay: i * 0.05 }}
                        />
                      </div>
                    </div>
                    <span className="font-mono text-[11px] text-[#a1a1a1] w-12 text-right">
                      {(feat.importance * 100).toFixed(1)}%
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </GradientCard>

        {/* Mining History */}
        <GradientCard accent="neutral" delay={0.2}>
          <h2 className="text-sm font-medium text-[#a1a1a1] mb-4">알파 마이닝 히스토리</h2>
          {miningLoading ? (
            <p className="text-xs text-[#a1a1a1]">로딩 중...</p>
          ) : miningError ? (
            <p className="text-sm text-red-400">{miningError}</p>
          ) : mining.length === 0 ? (
            <p className="text-sm text-[#a1a1a1]">데이터가 없습니다</p>
          ) : (
            <div className="space-y-3">
              {mining.map((run) => (
                <div key={run.timestamp} className="border border-[#2e2e2e] rounded-lg p-3">
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-xs text-[#a1a1a1]">{run.timestamp}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                      run.promoted > 0 ? "bg-emerald-500/10 text-emerald-400" : "bg-zinc-800 text-[#a1a1a1]"
                    }`}>
                      {run.promoted > 0 ? `${run.promoted} PROMOTED` : "NO PROMOTION"}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-[11px]">
                    <div>
                      <div className="text-zinc-600">후보</div>
                      <div className="text-white font-mono">{run.candidates_tested}</div>
                    </div>
                    <div>
                      <div className="text-zinc-600">통과</div>
                      <div className="text-white font-mono">{run.passed}</div>
                    </div>
                    <div>
                      <div className="text-zinc-600">최고 SR</div>
                      <div className={`font-mono ${run.best_sharpe > 0.3 ? "text-emerald-400" : "text-[#a1a1a1]"}`}>
                        {run.best_sharpe.toFixed(2)}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
              <p className="text-[10px] text-zinc-600 text-center">
                마이닝은 매월 1일 자동 실행 (5중 게이트 검증)
              </p>
            </div>
          )}
        </GradientCard>
      </div>
    </div>
  );
}
