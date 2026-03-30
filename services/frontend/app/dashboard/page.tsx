"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";
import { LiveFeed } from "../../components/live-feed";

export default function DashboardPage() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);

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
          <h3 className="mb-3 text-lg font-semibold">Gateway Summary</h3>
          <pre className="overflow-x-auto text-xs text-white/80">{JSON.stringify(data, null, 2)}</pre>
        </div>
      </section>
      <LiveFeed />
    </main>
  );
}
