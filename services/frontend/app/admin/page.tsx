"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AdminGuard } from "../../components/admin-guard";
import { gatewayFetch } from "../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  AnimatedNumber,
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
        setActionResult({ type: "ok", message: "Emergency stop executed." });
      } else if (action === "enable-live") {
        await gatewayFetch("/admin/execution/enable-live", { method: "POST" });
        setActionResult({ type: "ok", message: "Live trading enabled." });
      } else {
        await gatewayFetch("/admin/execution/preflight", { method: "POST" });
        setActionResult({ type: "ok", message: "Pre-flight checks passed." });
      }
      await load();
    } catch (err) {
      setActionResult({ type: "error", message: err instanceof Error ? err.message : "Action failed" });
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
    live: "bg-green-500",
    shadow: "bg-yellow-500",
    stopped: "bg-red-500",
    unknown: "bg-neutral-300",
  };

  if (!loaded) {
    return (
      <AdminGuard>
        <main className="grid gap-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="card animate-pulse">
                <div className="mx-auto h-4 w-20 rounded bg-neutral-200" />
                <div className="mx-auto mt-3 h-8 w-16 rounded bg-neutral-200" />
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
          <section className="card">
            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Admin</p>
            <h2 className="mt-2 text-3xl font-semibold text-neutral-900">Operator Control Surface</h2>
            <p className="mt-3 text-neutral-500">
              System overview, quick actions, and navigation to detailed control panels.
            </p>
          </section>

          {/* Overview Cards */}
          <StaggerContainer className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StaggerItem>
              <div className="card text-center">
                <p className="text-sm text-neutral-500">Services</p>
                <p className="mt-1 text-3xl font-bold text-neutral-900">
                  <AnimatedNumber value={serviceCount} />
                </p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="card text-center">
                <p className="text-sm text-neutral-500">Uptime</p>
                <p className="mt-1 text-3xl font-bold text-neutral-900">
                  {uptimeHours !== null ? <><AnimatedNumber value={uptimeHours} decimals={0} />h</> : "--"}
                </p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="card text-center">
                <p className="text-sm text-neutral-500">Mode</p>
                <div className="mt-2 flex items-center justify-center gap-2">
                  <span className={`inline-block h-3 w-3 rounded-full ${modeColors[currentMode]}`} />
                  <span className="text-lg font-semibold capitalize text-neutral-900">{currentMode}</span>
                </div>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="card text-center">
                <p className="text-sm text-neutral-500">DLQ Messages</p>
                <p className="mt-1 text-3xl font-bold text-yellow-600">
                  {dlqStats ? <AnimatedNumber value={dlqStats.total_messages} /> : "--"}
                </p>
              </div>
            </StaggerItem>
          </StaggerContainer>

          {/* Quick Actions */}
          <section className="card">
            <h3 className="mb-4 text-xl font-semibold text-neutral-900">Quick Actions</h3>
            <AnimatePresence>
              {actionResult && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.25 }}
                  className={`mb-4 rounded-lg px-4 py-3 text-sm ${
                    actionResult.type === "ok" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                  }`}
                >
                  {actionResult.message}
                </motion.div>
              )}
            </AnimatePresence>
            <StaggerContainer className="flex flex-wrap gap-3">
              <StaggerItem>
                <button
                  className="btn-danger px-6 py-3 font-semibold ring-2 ring-red-200 animate-pulse"
                  disabled={actionLoading === "emergency-stop"}
                  onClick={() => handleAction("emergency-stop")}
                >
                  {actionLoading === "emergency-stop" ? "Stopping..." : "Emergency Stop"}
                </button>
              </StaggerItem>
              <StaggerItem>
                <button
                  className="rounded-lg bg-green-600 px-6 py-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
                  disabled={actionLoading === "enable-live"}
                  onClick={() => handleAction("enable-live")}
                >
                  {actionLoading === "enable-live" ? "Enabling..." : "Enable Live"}
                </button>
              </StaggerItem>
              <StaggerItem>
                <button
                  className="btn-primary px-6 py-3 font-semibold"
                  disabled={actionLoading === "preflight"}
                  onClick={() => handleAction("preflight")}
                >
                  {actionLoading === "preflight" ? "Running..." : "Run Pre-flight"}
                </button>
              </StaggerItem>
            </StaggerContainer>
          </section>

          {/* Execution Config */}
          {execConfig && (
            <section className="card">
              <h3 className="mb-4 text-xl font-semibold text-neutral-900">Execution Config</h3>
              <StaggerContainer className="grid gap-3 sm:grid-cols-3">
                <StaggerItem>
                  <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                    <p className="text-xs text-neutral-400">live_trading_enabled</p>
                    <p className={`mt-1 text-lg font-semibold ${execConfig.live_trading_enabled ? "text-green-600" : "text-red-600"}`}>
                      {execConfig.live_trading_enabled ? "true" : "false"}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                    <p className="text-xs text-neutral-400">default_shadow_mode</p>
                    <p className={`mt-1 text-lg font-semibold ${execConfig.default_shadow_mode ? "text-yellow-600" : "text-neutral-600"}`}>
                      {execConfig.default_shadow_mode ? "true" : "false"}
                    </p>
                  </div>
                </StaggerItem>
                <StaggerItem>
                  <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                    <p className="text-xs text-neutral-400">allowed_exchanges</p>
                    <p className="mt-1 text-lg font-semibold text-neutral-900">
                      {execConfig.allowed_exchanges?.join(", ") || "none"}
                    </p>
                  </div>
                </StaggerItem>
              </StaggerContainer>
            </section>
          )}

          {/* DLQ Stats */}
          {dlqStats && dlqStats.streams && Object.keys(dlqStats.streams).length > 0 && (
            <section className="card">
              <h3 className="mb-4 text-xl font-semibold text-neutral-900">DLQ Streams</h3>
              <StaggerContainer className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {Object.entries(dlqStats.streams).map(([stream, count]) => (
                  <StaggerItem key={stream}>
                    <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
                      <span className="text-sm text-neutral-600">{stream}</span>
                      <span className="text-lg font-bold text-yellow-600">{count}</span>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            </section>
          )}

          {/* Navigation */}
          <StaggerContainer className="grid gap-4 sm:grid-cols-3">
            <StaggerItem>
              <Link href="/admin/trading" className="card block transition hover:border-neutral-300 hover:shadow-md">
                <h3 className="text-xl font-semibold text-neutral-900">Live Trading Control</h3>
                <p className="mt-2 text-neutral-500">Pre-flight checks, enable live, emergency stop, and execution config.</p>
              </Link>
            </StaggerItem>
            <StaggerItem>
              <Link href="/admin/system" className="card block transition hover:border-neutral-300 hover:shadow-md">
                <h3 className="text-xl font-semibold text-neutral-900">System Health</h3>
                <p className="mt-2 text-neutral-500">Service health grid, events, and DLQ management.</p>
              </Link>
            </StaggerItem>
            <StaggerItem>
              <Link href="/admin/users" className="card block transition hover:border-neutral-300 hover:shadow-md">
                <h3 className="text-xl font-semibold text-neutral-900">User Management</h3>
                <p className="mt-2 text-neutral-500">User list, role management, and stats.</p>
              </Link>
            </StaggerItem>
          </StaggerContainer>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
