"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  motion,
} from "../../components/motion";

interface SignalComponent {
  name?: string;
  value?: number;
  weight?: number;
}

interface Signal {
  asset: string;
  signal_score: number;
  direction: string;
  feature_timestamp?: string;
  components?: Record<string, unknown> | SignalComponent[];
  model_version?: string;
  confidence?: number;
}

function formatScore(score: number): string {
  return score.toLocaleString(undefined, {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}

function directionBadgeClass(direction: string): string {
  const d = direction.toUpperCase();
  if (d === "LONG" || d === "BUY") return "bg-green-50 text-green-700";
  if (d === "SHORT" || d === "SELL") return "bg-red-50 text-red-700";
  return "bg-neutral-100 text-neutral-500";
}

function scoreBarWidth(score: number): number {
  // Score ranges roughly -1 to 1; normalize to 0-100
  return Math.min(100, Math.max(0, (score + 1) * 50));
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    gatewayFetch("/signals")
      .then((data) => {
        setSignals(Array.isArray(data) ? (data as Signal[]) : []);
        setError(null);
      })
      .catch((e) => {
        setSignals([]);
        setError(e instanceof Error ? e.message : "시그널 로드 실패");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageTransition>
      <main className="grid gap-6">
        <section className="card">
          <h2 className="mb-4 text-2xl font-semibold text-neutral-900">시그널 뷰</h2>
          <ChartPlaceholder />
        </section>

        {loading ? (
          <StaggerContainer className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <StaggerItem key={i}>
                <div className="card animate-pulse space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="h-4 w-20 rounded bg-neutral-200" />
                    <div className="h-5 w-14 rounded-full bg-neutral-200" />
                  </div>
                  <div className="h-8 w-32 rounded bg-neutral-200" />
                  <div className="h-1.5 w-full rounded-full bg-neutral-100" />
                  <div className="h-3 w-40 rounded bg-neutral-100" />
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
        ) : error ? (
          <div className="card">
            <p className="text-red-500">{error}</p>
            <p className="mt-2 text-sm text-neutral-500">
              로그인 상태와 시그널 서비스 연결을 확인해주세요.
            </p>
          </div>
        ) : signals.length === 0 ? (
          <div className="card">
            <p className="text-neutral-400">시그널 없음</p>
          </div>
        ) : (
          <StaggerContainer className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {signals.map((signal, idx) => (
              <StaggerItem
                key={`${signal.asset}-${signal.feature_timestamp ?? idx}`}
              >
                <article className="card">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium uppercase tracking-wide text-neutral-500">
                      {signal.asset}
                    </p>
                    <motion.span
                      initial={{ scale: 0, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      transition={{ type: "spring", stiffness: 300, damping: 20, delay: 0.15 }}
                      className={`badge ${directionBadgeClass(signal.direction)}`}
                    >
                      {signal.direction}
                    </motion.span>
                  </div>

                  <h3 className="mt-2 text-3xl font-semibold text-neutral-900">
                    {formatScore(signal.signal_score)}
                  </h3>

                  {/* Score bar */}
                  <div className="mt-3 h-1.5 w-full rounded-full bg-neutral-100">
                    <motion.div
                      className={`h-1.5 rounded-full ${
                        signal.signal_score >= 0 ? "bg-green-500" : "bg-red-500"
                      }`}
                      initial={{ width: 0 }}
                      animate={{ width: `${scoreBarWidth(signal.signal_score)}%` }}
                      transition={{ duration: 0.7, ease: "easeOut", delay: 0.2 }}
                    />
                  </div>

                  {signal.feature_timestamp ? (
                    <p className="mt-2 text-xs text-neutral-400">
                      {new Date(signal.feature_timestamp).toLocaleString()}
                    </p>
                  ) : null}

                  {signal.confidence != null ? (
                    <p className="mt-1 text-xs text-neutral-500">
                      신뢰도: {(signal.confidence * 100).toFixed(1)}%
                    </p>
                  ) : null}

                  {signal.model_version ? (
                    <p className="mt-1 text-xs text-neutral-400">
                      모델: {signal.model_version}
                    </p>
                  ) : null}

                  {signal.components ? (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs text-neutral-400 hover:text-neutral-700">
                        구성요소
                      </summary>
                      <pre className="mt-2 overflow-x-auto rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-xs text-neutral-600">
                        {JSON.stringify(signal.components, null, 2)}
                      </pre>
                    </details>
                  ) : null}
                </article>
              </StaggerItem>
            ))}
          </StaggerContainer>
        )}
      </main>
    </PageTransition>
  );
}
