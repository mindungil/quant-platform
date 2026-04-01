"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AdminGuard } from "../../components/admin-guard";
import { gatewayFetch } from "../../lib/api";

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

  async function load() {
    gatewayFetch("/admin/system/health").then((d) => setHealth(d)).catch(() => setHealth(null));
    gatewayFetch("/admin/execution/config").then((d) => setExecConfig(d)).catch(() => setExecConfig(null));
    gatewayFetch("/admin/dlq/stats").then((d) => setDlqStats(d)).catch(() => setDlqStats(null));
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
    unknown: "bg-white/30",
  };

  return (
    <AdminGuard>
      <main className="grid gap-6">
        {/* Header */}
        <section className="panel">
          <p className="text-sm uppercase tracking-[0.2em] text-mint">Admin</p>
          <h2 className="mt-2 text-3xl font-semibold">Operator Control Surface</h2>
          <p className="mt-3 text-white/75">
            System overview, quick actions, and navigation to detailed control panels.
          </p>
        </section>

        {/* Overview Cards */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="panel text-center">
            <p className="text-sm text-white/60">Services</p>
            <p className="mt-1 text-3xl font-bold text-mint">{serviceCount}</p>
          </div>
          <div className="panel text-center">
            <p className="text-sm text-white/60">Uptime</p>
            <p className="mt-1 text-3xl font-bold text-mint">
              {uptimeHours !== null ? `${uptimeHours}h` : "--"}
            </p>
          </div>
          <div className="panel text-center">
            <p className="text-sm text-white/60">Mode</p>
            <div className="mt-2 flex items-center justify-center gap-2">
              <span className={`inline-block h-3 w-3 rounded-full ${modeColors[currentMode]}`} />
              <span className="text-lg font-semibold capitalize">{currentMode}</span>
            </div>
          </div>
          <div className="panel text-center">
            <p className="text-sm text-white/60">DLQ Messages</p>
            <p className="mt-1 text-3xl font-bold text-sand">
              {dlqStats?.total_messages ?? "--"}
            </p>
          </div>
        </div>

        {/* Quick Actions */}
        <section className="panel">
          <h3 className="mb-4 text-xl font-semibold">Quick Actions</h3>
          {actionResult && (
            <div
              className={`mb-4 rounded-xl px-4 py-3 text-sm ${
                actionResult.type === "ok" ? "bg-green-900/40 text-green-200" : "bg-red-900/40 text-red-200"
              }`}
            >
              {actionResult.message}
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            <button
              className="rounded-full bg-red-600 px-6 py-3 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
              disabled={actionLoading === "emergency-stop"}
              onClick={() => handleAction("emergency-stop")}
            >
              {actionLoading === "emergency-stop" ? "Stopping..." : "Emergency Stop"}
            </button>
            <button
              className="rounded-full bg-green-600 px-6 py-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
              disabled={actionLoading === "enable-live"}
              onClick={() => handleAction("enable-live")}
            >
              {actionLoading === "enable-live" ? "Enabling..." : "Enable Live"}
            </button>
            <button
              className="rounded-full bg-sand px-6 py-3 text-sm font-semibold text-ink hover:bg-sand/80 disabled:opacity-50"
              disabled={actionLoading === "preflight"}
              onClick={() => handleAction("preflight")}
            >
              {actionLoading === "preflight" ? "Running..." : "Run Pre-flight"}
            </button>
          </div>
        </section>

        {/* Execution Config */}
        {execConfig && (
          <section className="panel">
            <h3 className="mb-4 text-xl font-semibold">Execution Config</h3>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-2xl bg-black/20 p-4">
                <p className="text-xs text-white/50">live_trading_enabled</p>
                <p className={`mt-1 text-lg font-semibold ${execConfig.live_trading_enabled ? "text-green-400" : "text-red-400"}`}>
                  {execConfig.live_trading_enabled ? "true" : "false"}
                </p>
              </div>
              <div className="rounded-2xl bg-black/20 p-4">
                <p className="text-xs text-white/50">default_shadow_mode</p>
                <p className={`mt-1 text-lg font-semibold ${execConfig.default_shadow_mode ? "text-yellow-400" : "text-white/80"}`}>
                  {execConfig.default_shadow_mode ? "true" : "false"}
                </p>
              </div>
              <div className="rounded-2xl bg-black/20 p-4">
                <p className="text-xs text-white/50">allowed_exchanges</p>
                <p className="mt-1 text-lg font-semibold text-mint">
                  {execConfig.allowed_exchanges?.join(", ") || "none"}
                </p>
              </div>
            </div>
          </section>
        )}

        {/* DLQ Stats */}
        {dlqStats && dlqStats.streams && Object.keys(dlqStats.streams).length > 0 && (
          <section className="panel">
            <h3 className="mb-4 text-xl font-semibold">DLQ Streams</h3>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(dlqStats.streams).map(([stream, count]) => (
                <div key={stream} className="flex items-center justify-between rounded-2xl bg-black/20 px-4 py-3">
                  <span className="text-sm text-white/70">{stream}</span>
                  <span className="text-lg font-bold text-sand">{count}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Navigation */}
        <div className="grid gap-4 sm:grid-cols-3">
          <Link href="/admin/trading" className="panel hover:bg-white/5">
            <h3 className="text-xl font-semibold">Live Trading Control</h3>
            <p className="mt-2 text-white/70">Pre-flight checks, enable live, emergency stop, and execution config.</p>
          </Link>
          <Link href="/admin/system" className="panel hover:bg-white/5">
            <h3 className="text-xl font-semibold">System Health</h3>
            <p className="mt-2 text-white/70">Service health grid, events, and DLQ management.</p>
          </Link>
          <Link href="/admin/users" className="panel hover:bg-white/5">
            <h3 className="text-xl font-semibold">User Management</h3>
            <p className="mt-2 text-white/70">User list, role management, and stats.</p>
          </Link>
        </div>
      </main>
    </AdminGuard>
  );
}
