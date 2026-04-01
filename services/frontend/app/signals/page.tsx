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

function directionColor(direction: string): string {
  const d = direction.toUpperCase();
  if (d === "LONG" || d === "BUY") return "text-green-400";
  if (d === "SHORT" || d === "SELL") return "text-red-400";
  return "text-yellow-400";
}

function directionBg(direction: string): string {
  const d = direction.toUpperCase();
  if (d === "LONG" || d === "BUY") return "bg-green-500/20";
  if (d === "SHORT" || d === "SELL") return "bg-red-500/20";
  return "bg-yellow-500/20";
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
      <section className="panel">
        <h2 className="mb-4 text-2xl font-semibold">Signal View</h2>
        <ChartPlaceholder />
      </section>

      {loading ? (
        <div className="panel animate-pulse">
          <p className="text-white/60">Loading signals...</p>
        </div>
      ) : error ? (
        <div className="panel">
          <p className="text-red-400">{error}</p>
          <p className="mt-2 text-sm text-white/60">
            Make sure you are logged in and the signal service is running.
          </p>
        </div>
      ) : signals.length === 0 ? (
        <div className="panel">
          <p className="text-white/50">No signals available yet.</p>
        </div>
      ) : (
        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {signals.map((signal, idx) => (
            <article
              key={`${signal.asset}-${signal.feature_timestamp ?? idx}`}
              className="panel"
            >
              <div className="flex items-center justify-between">
                <p className="text-sm uppercase tracking-[0.2em] text-mint">
                  {signal.asset}
                </p>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${directionBg(signal.direction)} ${directionColor(signal.direction)}`}
                >
                  {signal.direction}
                </span>
              </div>

              <h3 className="mt-2 text-3xl font-semibold">
                {formatScore(signal.signal_score)}
              </h3>

              {/* Score bar */}
              <div className="mt-3 h-2 w-full rounded-full bg-white/10">
                <div
                  className={`h-2 rounded-full ${
                    signal.signal_score >= 0 ? "bg-green-500/70" : "bg-red-500/70"
                  }`}
                  style={{ width: scoreBarWidth(signal.signal_score) }}
                />
              </div>

              {signal.feature_timestamp ? (
                <p className="mt-2 text-xs text-white/40">
                  {new Date(signal.feature_timestamp).toLocaleString()}
                </p>
              ) : null}

              {signal.confidence != null ? (
                <p className="mt-1 text-xs text-white/50">
                  Confidence: {(signal.confidence * 100).toFixed(1)}%
                </p>
              ) : null}

              {signal.model_version ? (
                <p className="mt-1 text-xs text-white/40">
                  Model: {signal.model_version}
                </p>
              ) : null}

              {signal.components ? (
                <details className="mt-3">
                  <summary className="cursor-pointer text-xs text-white/50 hover:text-white/80">
                    Components
                  </summary>
                  <pre className="mt-2 overflow-x-auto rounded-xl bg-black/20 p-3 text-xs text-white/70">
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
