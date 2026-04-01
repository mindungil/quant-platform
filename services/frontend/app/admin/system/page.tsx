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
        <section className="card">
          <h2 className="text-3xl font-semibold text-neutral-900">System Health</h2>
          <p className="mt-2 text-neutral-500">
            Service health grid, recent events, and dead-letter queue management.
          </p>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        </section>

        {/* Service Health Grid */}
        <section>
          <h3 className="mb-3 text-lg font-semibold text-neutral-900">Services</h3>
          {health?.services ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {Object.entries(health.services).map(([name, svc]) => (
                <div key={name} className="card flex items-start gap-3">
                  <span className={`mt-1 inline-block h-3 w-3 flex-shrink-0 rounded-full ${statusColor(svc.status)}`} />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-neutral-900">{name}</p>
                    <p className="text-xs text-neutral-400">
                      {svc.status}
                      {svc.latency_ms !== undefined && ` / ${svc.latency_ms}ms`}
                    </p>
                    {svc.message && <p className="mt-1 text-xs text-neutral-400">{svc.message}</p>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="card text-sm text-neutral-400">Loading service health...</div>
          )}
        </section>

        {/* DLQ Management */}
        <section className="card">
          <h3 className="mb-4 text-xl font-semibold text-neutral-900">Dead Letter Queue</h3>
          {dlqStats ? (
            <>
              <p className="mb-3 text-sm text-neutral-500">
                Total messages: <span className="font-semibold text-yellow-600">{dlqStats.total_messages}</span>
              </p>
              {dlqStats.streams && Object.keys(dlqStats.streams).length > 0 ? (
                <div className="space-y-2">
                  {Object.entries(dlqStats.streams).map(([stream, count]) => (
                    <div
                      key={stream}
                      className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3"
                    >
                      <div>
                        <p className="text-sm font-medium text-neutral-900">{stream}</p>
                        <p className="text-xs text-neutral-400">{count} message{count !== 1 ? "s" : ""}</p>
                      </div>
                      <button
                        className="btn-primary text-xs disabled:opacity-50"
                        disabled={reprocessing === stream}
                        onClick={() => reprocessStream(stream)}
                      >
                        {reprocessing === stream ? "Processing..." : "Reprocess"}
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-neutral-400">No DLQ streams.</p>
              )}
            </>
          ) : (
            <p className="text-sm text-neutral-400">Loading DLQ stats...</p>
          )}
        </section>

        {/* Recent Events */}
        <section className="card">
          <h3 className="mb-4 text-xl font-semibold text-neutral-900">Recent System Events</h3>
          {events.length > 0 ? (
            <div className="space-y-2">
              {events.map((event, index) => (
                <div
                  key={`${event.event_id ?? index}-${index}`}
                  className="rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-medium text-neutral-900">{event.source}</span>
                    <span className="badge bg-neutral-100 text-neutral-500">{event.type}</span>
                    <span className="ml-auto text-xs text-neutral-400">
                      {event.timestamp ? new Date(event.timestamp).toLocaleString() : ""}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-neutral-600">{event.message}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-neutral-400">No recent events.</p>
          )}
        </section>
      </main>
    </AdminGuard>
  );
}
