"use client";

import { useEffect, useState, useCallback } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  motion,
} from "../../../components/motion";

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
  total: number;
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
      setError(err instanceof Error ? err.message : "재처리 실패");
    } finally {
      setReprocessing(null);
    }
  }

  const isHealthy = (status: string) => status === "ok" || status === "healthy";

  const statusDot = (status: string) => {
    if (isHealthy(status)) return "bg-emerald-400";
    if (status === "degraded" || status === "warning") return "bg-amber-400";
    return "bg-red-400";
  };

  return (
    <AdminGuard>
      <PageTransition>
        <main className="grid gap-6">
          {/* Header */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">SYSTEM</p>
            <h2 className="mt-1 text-2xl font-semibold text-white">시스템 상태</h2>
            <p className="mt-1 text-sm text-neutral-500">
              서비스 상태, 최근 이벤트, 데드레터 큐 관리
            </p>
            {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
          </section>

          {/* Service Health Grid */}
          <section>
            <p className="mb-3 text-sm font-medium uppercase tracking-wider text-neutral-400">SERVICES</p>
            {health?.services ? (
              <StaggerContainer className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {Object.entries(health.services).map(([name, svc]) => (
                  <StaggerItem key={name}>
                    <div className="flex items-start gap-3 rounded border border-white/[0.06] bg-white/[0.03] p-4 transition hover:border-white/[0.10]">
                      <span
                        className={`mt-1 inline-block h-2 w-2 flex-shrink-0 rounded-full ${statusDot(svc.status)}`}
                      />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-white">{name}</p>
                        <p className="font-mono text-xs text-neutral-400">
                          {svc.status}
                          {svc.latency_ms !== undefined && ` / ${svc.latency_ms}ms`}
                        </p>
                        {svc.message && <p className="mt-1 text-xs text-neutral-400">{svc.message}</p>}
                      </div>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            ) : (
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-sm text-neutral-400">서비스 상태 로딩 중...</div>
            )}
          </section>

          {/* DLQ Management */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">DEAD LETTER QUEUE</p>
            <h3 className="mt-2 text-lg font-semibold text-white">데드레터 큐</h3>
            {dlqStats ? (
              <>
                <p className="mt-3 mb-3 text-sm text-neutral-500">
                  전체 메시지: <span className="font-mono font-semibold text-white">{dlqStats.total}</span>
                </p>
                {dlqStats.streams && Object.keys(dlqStats.streams).length > 0 ? (
                  <StaggerContainer className="space-y-2">
                    {Object.entries(dlqStats.streams).map(([stream, count]) => (
                      <StaggerItem key={stream}>
                        <div className="flex items-center justify-between rounded border border-white/[0.06] bg-white/[0.03] px-4 py-3">
                          <div>
                            <p className="text-sm font-medium text-white">{stream}</p>
                            <p className="font-mono text-xs text-neutral-400">{count} message{count !== 1 ? "s" : ""}</p>
                          </div>
                          <button
                            className="btn-primary inline-flex items-center gap-2 text-xs disabled:opacity-50"
                            disabled={reprocessing === stream}
                            onClick={() => reprocessStream(stream)}
                          >
                            {reprocessing === stream && (
                              <motion.span
                                className="inline-block h-3 w-3 rounded-full border-2 border-white border-t-transparent"
                                animate={{ rotate: 360 }}
                                transition={{ repeat: Infinity, duration: 0.6, ease: "linear" }}
                              />
                            )}
                            {reprocessing === stream ? "처리 중..." : "재처리"}
                          </button>
                        </div>
                      </StaggerItem>
                    ))}
                  </StaggerContainer>
                ) : (
                  <p className="text-sm text-neutral-400">DLQ 스트림 없음</p>
                )}
              </>
            ) : (
              <p className="mt-4 text-sm text-neutral-400">DLQ 통계 로딩 중...</p>
            )}
          </section>

          {/* Recent Events */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">EVENTS</p>
            <h3 className="mt-2 text-lg font-semibold text-white">최근 시스템 이벤트</h3>
            {events.length > 0 ? (
              <StaggerContainer className="mt-4 space-y-2">
                {events.map((event, index) => (
                  <StaggerItem key={`${event.event_id ?? index}-${index}`}>
                    <div className="rounded border border-white/[0.06] bg-white/[0.03] px-4 py-3">
                      <div className="flex items-center gap-3">
                        <span className="text-xs font-medium text-white">{event.source}</span>
                        <span className="inline-flex items-center rounded-full bg-white/[0.06] px-2 py-0.5 text-xs font-medium text-neutral-400">{event.type}</span>
                        <span className="ml-auto text-xs text-neutral-400">
                          {event.timestamp ? new Date(event.timestamp).toLocaleString() : ""}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-neutral-400">{event.message}</p>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            ) : (
              <p className="mt-4 text-sm text-neutral-400">최근 이벤트 없음</p>
            )}
          </section>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
