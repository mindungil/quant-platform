"use client";

import { useEffect, useState, useCallback } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";

type ServiceHealth = {
  status: string;
  latency_ms?: number;
  message?: string;
};

type HealthResponse = {
  status: string;
  services: Record<string, ServiceHealth>;
  uptime_seconds?: number;
};

type SystemEvent = {
  event_id?: string;
  type: string;
  source: string;
  message: string;
  timestamp: string;
};

type DlqStats = {
  total_messages: number;
  streams: Record<string, number>;
};

export default function AdminSystemPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [dlqStats, setDlqStats] = useState<DlqStats | null>(null);
  const [reprocessing, setReprocessing] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    gatewayFetch("/admin/system/health")
      .then((d) => setHealth(d))
      .catch(() => setHealth(null));
    gatewayFetch("/admin/system/events?limit=25")
      .then((response) => setEvents(response.items ?? []))
      .catch(() => setEvents([]));
    gatewayFetch("/admin/dlq/stats")
      .then((d) => setDlqStats(d))
      .catch(() => setDlqStats(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function reprocessStream(stream: string) {
    setReprocessing(stream);
    setError("");
    try {
      await gatewayFetch(`/admin/dlq/reprocess/${stream}`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reprocess failed");
    } finally {
      setReprocessing(null);
    }
  }

  const statusColor = (status: string) => {
    if (status === "ok" || status === "healthy") return "bg-green-500";
    if (status === "degraded" || status === "warning") return "bg-yellow-500";
    return "bg-red-500";
  };

  return (
    <AdminGuard>
      <main className="grid gap-6">
        {/* Header */}
        <section className="panel">
          <h2 className="text-3xl font-semibold">System Health</h2>
          <p className="mt-2 text-white/70">
            Service health grid, recent events, and dead-letter queue management.
          </p>
          {error && <p className="mt-3 text-sm text-red-300">{error}</p>}
        </section>

        {/* Service Health Grid */}
        <section>
          <h3 className="mb-3 text-lg font-semibold text-white/90">Services</h3>
          {health?.services ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {Object.entries(health.services).map(([name, svc]) => (
                <div key={name} className="panel flex items-start gap-3">
                  <span className={`mt-1 inline-block h-3 w-3 flex-shrink-0 rounded-full ${statusColor(svc.status)}`} />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold">{name}</p>
                    <p className="text-xs text-white/50">
                      {svc.status}
                      {svc.latency_ms !== undefined && ` / ${svc.latency_ms}ms`}
                    </p>
                    {svc.message && <p className="mt-1 text-xs text-white/40">{svc.message}</p>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="panel text-sm text-white/50">Loading service health...</div>
          )}
        </section>

        {/* DLQ Management */}
        <section className="panel">
          <h3 className="mb-4 text-xl font-semibold">Dead Letter Queue</h3>
          {dlqStats ? (
            <>
              <p className="mb-3 text-sm text-white/60">
                Total messages: <span className="font-semibold text-sand">{dlqStats.total_messages}</span>
              </p>
              {dlqStats.streams && Object.keys(dlqStats.streams).length > 0 ? (
                <div className="space-y-2">
                  {Object.entries(dlqStats.streams).map(([stream, count]) => (
                    <div
                      key={stream}
                      className="flex items-center justify-between rounded-2xl bg-black/20 px-4 py-3"
                    >
                      <div>
                        <p className="text-sm font-medium">{stream}</p>
                        <p className="text-xs text-white/50">{count} message{count !== 1 ? "s" : ""}</p>
                      </div>
                      <button
                        className="rounded-full bg-sand px-4 py-2 text-xs font-semibold text-ink hover:bg-sand/80 disabled:opacity-50"
                        disabled={reprocessing === stream}
                        onClick={() => reprocessStream(stream)}
                      >
                        {reprocessing === stream ? "Processing..." : "Reprocess"}
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-white/50">No DLQ streams.</p>
              )}
            </>
          ) : (
            <p className="text-sm text-white/50">Loading DLQ stats...</p>
          )}
        </section>

        {/* Recent Events */}
        <section className="panel">
          <h3 className="mb-4 text-xl font-semibold">Recent System Events</h3>
          {events.length > 0 ? (
            <div className="space-y-2">
              {events.map((event, index) => (
                <div
                  key={`${event.event_id ?? index}-${index}`}
                  className="rounded-2xl bg-black/20 px-4 py-3"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-medium text-mint">{event.source}</span>
                    <span className="text-xs text-white/40">{event.type}</span>
                    <span className="ml-auto text-xs text-white/30">
                      {event.timestamp ? new Date(event.timestamp).toLocaleString() : ""}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-white/70">{event.message}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-white/50">No recent events.</p>
          )}
        </section>
      </main>
    </AdminGuard>
  );
}
