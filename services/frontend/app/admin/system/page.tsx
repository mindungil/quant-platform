"use client";

import { useEffect, useState } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";

export default function AdminSystemPage() {
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [executionConfig, setExecutionConfig] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState("");

  async function load() {
    gatewayFetch("/admin/system/health").then(setHealth).catch(() => setHealth(null));
    gatewayFetch("/admin/system/events?limit=25")
      .then((response) => setEvents(response.items ?? []))
      .catch(() => setEvents([]));
    gatewayFetch("/admin/execution/config")
      .then(setExecutionConfig)
      .catch((err) => setError(err instanceof Error ? err.message : "failed_to_load_execution_config"));
  }

  useEffect(() => {
    load();
  }, []);

  async function updateExecution(next: Record<string, any>) {
    await gatewayFetch("/admin/execution/config", {
      method: "PATCH",
      body: JSON.stringify(next),
    });
    await load();
  }

  return (
    <AdminGuard>
      <main className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <section className="panel">
          <h2 className="mb-4 text-2xl font-semibold">System Health</h2>
          <pre className="overflow-x-auto text-xs text-white/80">{JSON.stringify(health, null, 2)}</pre>
          <div className="mt-6 rounded-2xl bg-black/20 p-4">
            <h3 className="text-lg font-semibold">Execution Config</h3>
            {error ? <p className="mt-2 text-sm text-red-300">{error}</p> : null}
            <pre className="mt-3 overflow-x-auto text-xs text-white/80">{JSON.stringify(executionConfig, null, 2)}</pre>
            {executionConfig ? (
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  className="rounded-full bg-sand px-4 py-2 text-ink"
                  onClick={() => updateExecution({ ...executionConfig, live_trading_enabled: !executionConfig.live_trading_enabled })}
                >
                  {executionConfig.live_trading_enabled ? "Disable Live Trading" : "Enable Live Trading"}
                </button>
                <button
                  className="rounded-full border border-white/20 px-4 py-2"
                  onClick={() => updateExecution({ ...executionConfig, default_shadow_mode: !executionConfig.default_shadow_mode })}
                >
                  Toggle Default Shadow Mode
                </button>
              </div>
            ) : null}
          </div>
        </section>
        <section className="panel">
          <h2 className="mb-4 text-2xl font-semibold">Recent Realtime Events</h2>
          <div className="space-y-3">
            {events.map((event, index) => (
              <pre key={`${event.event_id ?? index}-${index}`} className="overflow-x-auto rounded-2xl bg-black/20 p-3 text-xs text-white/80">
                {JSON.stringify(event, null, 2)}
              </pre>
            ))}
          </div>
        </section>
      </main>
    </AdminGuard>
  );
}
