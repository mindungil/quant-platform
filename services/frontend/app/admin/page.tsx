"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AdminGuard } from "../../components/admin-guard";
import { gatewayFetch } from "../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  AnimatePresence,
  motion,
} from "../../components/motion";

type DlqStats = {
  total_messages: number;
  streams: Record<string, number>;
};

type ExecutionConfig = {
  live_trading_enabled: boolean;
  allowed_exchanges: string[];
  default_shadow_mode: boolean;
};

type HealthInfo = {
  status: string;
  services: Record<string, { status: string }>;
  uptime_seconds?: number;
};

export default function AdminPage() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [execConfig, setExecConfig] = useState<ExecutionConfig | null>(null);
  const [dlqStats, setDlqStats] = useState<DlqStats | null>(null);
  const [actionLoading, setActionLoading] = useState("");
  const [actionResult, setActionResult] = useState<{ type: "ok" | "error"; message: string } | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    await Promise.allSettled([
      gatewayFetch("/admin/system/health").then((d) => setHealth(d)).catch(() => setHealth(null)),
      gatewayFetch("/admin/execution/config").then((d) => setExecConfig(d)).catch(() => setExecConfig(null)),
      gatewayFetch("/admin/dlq/stats").then((d) => setDlqStats(d)).catch(() => setDlqStats(null)),
    ]);
    setLoaded(true);
  }

  useEffect(() => {
    load();
  }, []);

  async function handleAction(action: "emergency-stop" | "enable-live" | "preflight") {
    setActionLoading(action);
    setActionResult(null);
    try {
      if (action === "emergency-stop") {
        await gatewayFetch("/admin/execution/emergency-stop", { method: "POST" });
        setActionResult({ type: "ok", message: "긴급 정지가 실행되었습니다." });
      } else if (action === "enable-live") {
        await gatewayFetch("/admin/execution/enable-live", { method: "POST" });
        setActionResult({ type: "ok", message: "실시간 트레이딩이 활성화되었습니다." });
      } else {
        await gatewayFetch("/admin/execution/preflight", { method: "POST" });
        setActionResult({ type: "ok", message: "사전 점검을 통과했습니다." });
      }
      await load();
    } catch (err) {
      setActionResult({ type: "error", message: err instanceof Error ? err.message : "작업 실패" });
    } finally {
      setActionLoading("");
    }
  }

  const serviceCount = health?.services ? Object.keys(health.services).length : 0;
  const uptimeHours = health?.uptime_seconds ? Math.floor(health.uptime_seconds / 3600) : null;

  const currentMode = execConfig
    ? execConfig.live_trading_enabled
      ? "live"
      : execConfig.default_shadow_mode
        ? "shadow"
        : "stopped"
    : "unknown";

  const modeColors: Record<string, string> = {
    live: "bg-emerald-400",
    shadow: "bg-neutral-400",
    stopped: "bg-red-400",
    unknown: "bg-neutral-500",
  };

  if (!loaded) {
    return (
      <AdminGuard>
        <main className="grid gap-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="rounded border border-white/[0.06] bg-white/[0.03] p-6 animate-pulse">
                <div className="mx-auto h-4 w-20 rounded bg-white/[0.06]" />
                <div className="mx-auto mt-3 h-8 w-16 rounded bg-white/[0.06]" />
              </div>
            ))}
          </div>
        </main>
      </AdminGuard>
    );
  }

  return (
    <AdminGuard>
      <PageTransition>
        <main className="grid gap-6">
          {/* Header */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">ADMIN</p>
            <h2 className="mt-1 text-2xl font-semibold text-white">운영 제어판</h2>
            <p className="mt-2 text-neutral-500">
              시스템 개요, 빠른 작업, 세부 제어판 탐색
            </p>
          </section>

          {/* Overview Cards */}
          <StaggerContainer className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">서비스</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-white">
                  {serviceCount}
                </p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">가동 시간</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-white">
                  {uptimeHours !== null ? `${uptimeHours}h` : "--"}
                </p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">모드</p>
                <div className="mt-2 flex items-center justify-center gap-2">
                  <span className={`inline-block h-2 w-2 rounded-full ${modeColors[currentMode]}`} />
                  <span className="font-mono text-lg font-semibold uppercase text-white">{currentMode}</span>
                </div>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-white/[0.06] bg-white/[0.03] p-6 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">DLQ 메시지</p>
                <p className="mt-1 font-mono text-2xl font-semibold text-white">
                  {dlqStats ? dlqStats.total_messages : "--"}
                </p>
              </div>
            </StaggerItem>
          </StaggerContainer>

          {/* Quick Actions */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">ACTIONS</p>
            <h3 className="mt-2 text-lg font-semibold text-white">빠른 작업</h3>
            <AnimatePresence>
              {actionResult && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25 }}
                  className={`mt-4 rounded border px-4 py-3 text-sm ${
                    actionResult.type === "ok" ? "border-white/[0.06] text-emerald-400" : "border-white/[0.06] text-red-400"
                  }`}
                >
                  {actionResult.message}
                </motion.div>
              )}
            </AnimatePresence>
            <StaggerContainer className="mt-4 flex flex-wrap gap-3">
              <StaggerItem>
                <button
                  className="btn-danger px-6 py-3 font-semibold"
                  disabled={actionLoading === "emergency-stop"}
                  onClick={() => handleAction("emergency-stop")}
                >
                  {actionLoading === "emergency-stop" ? "정지 중..." : "긴급 정지"}
                </button>
              </StaggerItem>
              <StaggerItem>
                <button
                  className="rounded border border-white/[0.10] bg-white/[0.03] px-6 py-3 text-sm font-semibold text-white hover:bg-white/[0.06] disabled:opacity-50"
                  disabled={actionLoading === "enable-live"}
                  onClick={() => handleAction("enable-live")}
                >
                  {actionLoading === "enable-live" ? "활성화 중..." : "라이브 활성화"}
                </button>
              </StaggerItem>
              <StaggerItem>
                <button
                  className="btn-primary px-6 py-3 font-semibold"
                  disabled={actionLoading === "preflight"}
                  onClick={() => handleAction("preflight")}
                >
                  {actionLoading === "preflight" ? "실행 중..." : "사전 점검 실행"}
                </button>
              </StaggerItem>
            </StaggerContainer>
          </section>

          {/* Execution Config */}
          {execConfig && (
            <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">CONFIG</p>
              <h3 className="mt-2 text-lg font-semibold text-white">실행 설정</h3>
              <StaggerContainer className="mt-4 grid gap-3 sm:grid-cols-3">
                <StaggerItem>
                  <div className="rounded border border-white/[0.06] bg-white/[0.03] p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">live_trading_enabled</p>
                    <p className={`mt-1 font-mono text-lg font-semibold ${execConfig.live_trading_enabled ? "text-emerald-400" : "text-red-400"}`}>
                      {execConfig.live_trading_enabled ? "true" : "false"}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded border border-white/[0.06] bg-white/[0.03] p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">default_shadow_mode</p>
                    <p className="mt-1 font-mono text-lg font-semibold text-white">
                      {execConfig.default_shadow_mode ? "true" : "false"}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded border border-white/[0.06] bg-white/[0.03] p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">allowed_exchanges</p>
                    <p className="mt-1 font-mono text-lg font-semibold text-white">
                      {execConfig.allowed_exchanges?.join(", ") || "none"}
                    </p>
                  </div>
                </StaggerItem>
              </StaggerContainer>
            </section>
          )}

          {/* DLQ Stats */}
          {dlqStats && dlqStats.streams && Object.keys(dlqStats.streams).length > 0 && (
            <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">DLQ</p>
              <h3 className="mt-2 text-lg font-semibold text-white">DLQ 스트림</h3>
              <StaggerContainer className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {Object.entries(dlqStats.streams).map(([stream, count]) => (
                  <StaggerItem key={stream}>
                    <div className="flex items-center justify-between rounded border border-white/[0.06] bg-white/[0.03] px-4 py-3">
                      <span className="text-sm text-neutral-400">{stream}</span>
                      <span className="font-mono text-lg font-semibold text-white">{count}</span>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            </section>
          )}

          {/* Navigation */}
          <StaggerContainer className="grid gap-4 sm:grid-cols-3">
            <StaggerItem>
              <Link href="/admin/trading" className="block rounded border border-white/[0.06] bg-white/[0.03] p-6 transition hover:border-white/[0.10]">
                <h3 className="text-lg font-semibold text-white">실시간 트레이딩 제어</h3>
                <p className="mt-2 text-sm text-neutral-500">사전 점검, 라이브 활성화, 긴급 정지, 실행 설정</p>
              </Link>
            </StaggerItem>
            <StaggerItem>
              <Link href="/admin/system" className="block rounded border border-white/[0.06] bg-white/[0.03] p-6 transition hover:border-white/[0.10]">
                <h3 className="text-lg font-semibold text-white">시스템 상태</h3>
                <p className="mt-2 text-sm text-neutral-500">서비스 상태, 이벤트, DLQ 관리</p>
              </Link>
            </StaggerItem>
            <StaggerItem>
              <Link href="/admin/users" className="block rounded border border-white/[0.06] bg-white/[0.03] p-6 transition hover:border-white/[0.10]">
                <h3 className="text-lg font-semibold text-white">사용자 관리</h3>
                <p className="mt-2 text-sm text-neutral-500">사용자 목록, 역할 관리, 통계</p>
              </Link>
            </StaggerItem>
          </StaggerContainer>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
