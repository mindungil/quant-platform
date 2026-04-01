"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";

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

function scoreBarWidth(score: number): string {
  // Score ranges roughly -1 to 1; normalize to 0-100%
  const pct = Math.min(100, Math.max(0, (score + 1) * 50));
  return `${pct}%`;
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
        setError(e instanceof Error ? e.message : "Failed to load signals");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="grid gap-6">
      <section className="card">
        <h2 className="mb-4 text-2xl font-semibold text-neutral-900">Signal View</h2>
        <ChartPlaceholder />
      </section>

      {loading ? (
        <div className="card animate-pulse">
          <p className="text-neutral-400">Loading signals...</p>
        </div>
      ) : error ? (
        <div className="card">
          <p className="text-red-500">{error}</p>
          <p className="mt-2 text-sm text-neutral-500">
            Make sure you are logged in and the signal service is running.
          </p>
        </div>
      ) : signals.length === 0 ? (
        <div className="card">
          <p className="text-neutral-400">No signals available yet.</p>
        </div>
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {signals.map((signal, idx) => (
            <article
              key={`${signal.asset}-${signal.feature_timestamp ?? idx}`}
              className="card"
            >
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium uppercase tracking-wide text-neutral-500">
                  {signal.asset}
                </p>
                <span
                  className={`badge ${directionBadgeClass(signal.direction)}`}
                >
                  {signal.direction}
                </span>
              </div>

              <h3 className="mt-2 text-3xl font-semibold text-neutral-900">
                {formatScore(signal.signal_score)}
              </h3>

              {/* Score bar */}
              <div className="mt-3 h-1.5 w-full rounded-full bg-neutral-100">
                <div
                  className={`h-1.5 rounded-full ${
                    signal.signal_score >= 0 ? "bg-green-500" : "bg-red-500"
                  }`}
                  style={{ width: scoreBarWidth(signal.signal_score) }}
                />
              </div>

              {signal.feature_timestamp ? (
                <p className="mt-2 text-xs text-neutral-400">
                  {new Date(signal.feature_timestamp).toLocaleString()}
                </p>
              ) : null}

              {signal.confidence != null ? (
                <p className="mt-1 text-xs text-neutral-500">
                  Confidence: {(signal.confidence * 100).toFixed(1)}%
                </p>
              ) : null}

              {signal.model_version ? (
                <p className="mt-1 text-xs text-neutral-400">
                  Model: {signal.model_version}
                </p>
              ) : null}

              {signal.components ? (
                <details className="mt-3">
                  <summary className="cursor-pointer text-xs text-neutral-400 hover:text-neutral-700">
                    Components
                  </summary>
                  <pre className="mt-2 overflow-x-auto rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-xs text-neutral-600">
                    {JSON.stringify(signal.components, null, 2)}
                  </pre>
                </details>
              ) : null}
            </article>
          ))}
        </section>
      )}
    </main>
  );
}
