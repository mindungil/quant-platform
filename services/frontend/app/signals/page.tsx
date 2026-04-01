"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";

export default function SignalsPage() {
  const [signals, setSignals] = useState<any[]>([]);

  useEffect(() => {
    gatewayFetch("/signals").then(setSignals).catch(() => setSignals([]));
  }, []);

  return (
    <main className="grid gap-6">
      <section className="panel">
        <h2 className="mb-4 text-2xl font-semibold">Signal View</h2>
        <ChartPlaceholder />
      </section>
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {signals.map((signal) => (
          <article key={`${signal.asset}-${signal.feature_timestamp}`} className="panel">
            <p className="text-sm uppercase tracking-[0.2em] text-mint">{signal.asset}</p>
            <h3 className="mt-2 text-3xl font-semibold">{signal.signal_score}</h3>
            <p className="mt-2 text-white/70">{signal.direction}</p>
            <pre className="mt-4 overflow-x-auto text-xs text-white/70">{JSON.stringify(signal.components, null, 2)}</pre>
          </article>
        ))}
      </section>
    </main>
  );
}
