"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";
import { LiveFeed } from "../../components/live-feed";

export default function DashboardPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const settings = (data?.settings as Record<string, unknown> | undefined) ?? undefined;
  const execution = (settings?.execution as Record<string, unknown> | undefined) ?? null;

  useEffect(() => {
    gatewayFetch("/dashboard").then(setData).catch(() => setData(null));
  }, []);

  return (
    <main className="grid gap-6">
      <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="panel">
          <h2 className="mb-3 text-2xl font-semibold">Portfolio Dashboard</h2>
          <ChartPlaceholder />
        </div>
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Operator Snapshot</h3>
          <div className="space-y-3 text-sm text-white/80">
            <div className="rounded-2xl bg-black/20 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-mint">Active Strategy</p>
              <pre className="mt-2 overflow-x-auto text-xs">{JSON.stringify(data?.active_strategy ?? null, null, 2)}</pre>
            </div>
            <div className="rounded-2xl bg-black/20 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-mint">Portfolio</p>
              <pre className="mt-2 overflow-x-auto text-xs">{JSON.stringify(data?.portfolio ?? null, null, 2)}</pre>
            </div>
            <div className="rounded-2xl bg-black/20 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-mint">Execution Posture</p>
              <pre className="mt-2 overflow-x-auto text-xs">{JSON.stringify(execution, null, 2)}</pre>
            </div>
          </div>
        </div>
      </section>
      <section className="grid gap-6 lg:grid-cols-3">
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Latest Orders</h3>
          <pre className="overflow-x-auto text-xs text-white/80">{JSON.stringify(data?.orders ?? [], null, 2)}</pre>
        </div>
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Statistics</h3>
          <pre className="overflow-x-auto text-xs text-white/80">{JSON.stringify(data?.statistics ?? null, null, 2)}</pre>
        </div>
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Signals</h3>
          <pre className="overflow-x-auto text-xs text-white/80">{JSON.stringify(data?.signals ?? [], null, 2)}</pre>
        </div>
      </section>
      <LiveFeed />
    </main>
  );
}
