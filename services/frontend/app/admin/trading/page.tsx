"use client";

import { useEffect, useState, useCallback } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";

type PreflightCheck = {
  name: string;
  passed: boolean;
  message?: string;
};

type PreflightResult = {
  all_passed: boolean;
  checks: PreflightCheck[];
};

type ExecutionConfig = {
  live_trading_enabled: boolean;
  allowed_exchanges: string[];
  default_shadow_mode: boolean;
};

export default function AdminTradingPage() {
  const [execConfig, setExecConfig] = useState<ExecutionConfig | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [enableLoading, setEnableLoading] = useState(false);
  const [stopLoading, setStopLoading] = useState(false);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [enableConfirm, setEnableConfirm] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Editable config state
  const [editExchanges, setEditExchanges] = useState("");
  const [editShadow, setEditShadow] = useState(false);
  const [configDirty, setConfigDirty] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);

  const loadConfig = useCallback(async () => {
    try {
      const config = await gatewayFetch("/admin/execution/config");
      setExecConfig(config);
      setEditExchanges(config.allowed_exchanges?.join(", ") ?? "");
      setEditShadow(config.default_shadow_mode);
      setConfigDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load config");
    }
  }, []);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  async function runPreflight() {
    setPreflightLoading(true);
    setError("");
    setSuccess("");
    try {
      const result = await gatewayFetch("/admin/execution/preflight", { method: "POST" });
      setPreflight(result);
      if (result.all_passed) {
        setSuccess("All pre-flight checks passed.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pre-flight failed");
      setPreflight(null);
    } finally {
      setPreflightLoading(false);
    }
  }

  async function enableLive() {
    setEnableLoading(true);
    setError("");
    setSuccess("");
    try {
      await gatewayFetch("/admin/execution/enable-live", { method: "POST" });
      setSuccess("Live trading has been enabled.");
      setEnableConfirm(false);
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to enable live trading");
    } finally {
      setEnableLoading(false);
    }
  }

  async function emergencyStop() {
    setStopLoading(true);
    setError("");
    setSuccess("");
    try {
      await gatewayFetch("/admin/execution/emergency-stop", { method: "POST" });
      setSuccess("Emergency stop executed. All trading halted.");
      setStopConfirm(false);
      setPreflight(null);
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Emergency stop failed");
    } finally {
      setStopLoading(false);
    }
  }

  async function saveConfig() {
    setConfigSaving(true);
    setError("");
    setSuccess("");
    try {
      const exchanges = editExchanges
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      await gatewayFetch("/admin/execution/config", {
        method: "PATCH",
        body: JSON.stringify({
          allowed_exchanges: exchanges,
          default_shadow_mode: editShadow,
        }),
      });
      setSuccess("Execution config updated.");
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save config");
    } finally {
      setConfigSaving(false);
    }
  }

  const currentMode = execConfig
    ? execConfig.live_trading_enabled
      ? "live"
      : execConfig.default_shadow_mode
        ? "shadow"
        : "stopped"
    : "unknown";

  const statusStyles: Record<string, { bg: string; text: string; label: string }> = {
    live: { bg: "bg-green-500/20 border-green-500/40", text: "text-green-400", label: "LIVE" },
    shadow: { bg: "bg-yellow-500/20 border-yellow-500/40", text: "text-yellow-400", label: "SHADOW" },
    stopped: { bg: "bg-red-500/20 border-red-500/40", text: "text-red-400", label: "STOPPED" },
    unknown: { bg: "bg-white/5 border-white/20", text: "text-white/50", label: "UNKNOWN" },
  };

  const style = statusStyles[currentMode];

  return (
    <AdminGuard>
      <main className="grid gap-6">
        {/* Header with status */}
        <section className="panel">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-3xl font-semibold">Live Trading Control</h2>
              <p className="mt-2 text-white/70">
                Pre-flight checks, execution controls, and configuration.
              </p>
            </div>
            <div className={`rounded-xl border px-6 py-3 text-center ${style.bg}`}>
              <p className="text-xs text-white/50">Current Status</p>
              <p className={`text-2xl font-bold ${style.text}`}>{style.label}</p>
            </div>
          </div>
        </section>

        {/* Alerts */}
        {error && (
          <div className="rounded-xl bg-red-900/40 px-4 py-3 text-sm text-red-200">{error}</div>
        )}
        {success && (
          <div className="rounded-xl bg-green-900/40 px-4 py-3 text-sm text-green-200">{success}</div>
        )}

        {/* Pre-flight Checks */}
        <section className="panel">
          <div className="flex items-center justify-between">
            <h3 className="text-xl font-semibold">Pre-flight Checks</h3>
            <button
              className="rounded-full bg-sand px-5 py-2 text-sm font-semibold text-ink hover:bg-sand/80 disabled:opacity-50"
              disabled={preflightLoading}
              onClick={runPreflight}
            >
              {preflightLoading ? "Running..." : "Run Pre-flight"}
            </button>
          </div>
          {preflight && (
            <div className="mt-4 space-y-2">
              {preflight.checks.map((check) => (
                <div
                  key={check.name}
                  className="flex items-center gap-3 rounded-xl bg-black/20 px-4 py-3"
                >
                  <span
                    className={`inline-block h-3 w-3 flex-shrink-0 rounded-full ${
                      check.passed ? "bg-green-500" : "bg-red-500"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{check.name}</p>
                    {check.message && (
                      <p className="text-xs text-white/50">{check.message}</p>
                    )}
                  </div>
                  <span
                    className={`text-xs font-semibold ${check.passed ? "text-green-400" : "text-red-400"}`}
                  >
                    {check.passed ? "PASS" : "FAIL"}
                  </span>
                </div>
              ))}
              <div className={`mt-2 rounded-xl px-4 py-2 text-center text-sm font-semibold ${
                preflight.all_passed ? "bg-green-900/30 text-green-300" : "bg-red-900/30 text-red-300"
              }`}>
                {preflight.all_passed ? "All checks passed" : "Some checks failed"}
              </div>
            </div>
          )}
          {!preflight && !preflightLoading && (
            <p className="mt-4 text-sm text-white/50">
              Run pre-flight to verify credentials, exchange connectivity, risk limits, and active strategies.
            </p>
          )}
        </section>

        {/* Enable Live / Emergency Stop */}
        <div className="grid gap-4 sm:grid-cols-2">
          {/* Enable Live */}
          <section className="panel">
            <h3 className="mb-3 text-xl font-semibold">Enable Live Trading</h3>
            <p className="mb-4 text-sm text-white/60">
              Requires all pre-flight checks to pass before activation.
            </p>
            {!enableConfirm ? (
              <button
                className="w-full rounded-full bg-green-600 py-3 text-sm font-semibold text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!preflight?.all_passed || execConfig?.live_trading_enabled === true}
                onClick={() => setEnableConfirm(true)}
              >
                {execConfig?.live_trading_enabled ? "Already Live" : "Enable Live Trading"}
              </button>
            ) : (
              <div className="space-y-3">
                <p className="text-sm font-medium text-yellow-300">
                  Confirm: Enable live trading with real funds?
                </p>
                <div className="flex gap-3">
                  <button
                    className="flex-1 rounded-full bg-green-600 py-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
                    disabled={enableLoading}
                    onClick={enableLive}
                  >
                    {enableLoading ? "Enabling..." : "Yes, Enable Live"}
                  </button>
                  <button
                    className="flex-1 rounded-full border border-white/20 py-3 text-sm hover:bg-white/10"
                    onClick={() => setEnableConfirm(false)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </section>

          {/* Emergency Stop */}
          <section className="panel">
            <h3 className="mb-3 text-xl font-semibold">Emergency Stop</h3>
            <p className="mb-4 text-sm text-white/60">
              Immediately halt all trading operations and cancel open orders.
            </p>
            {!stopConfirm ? (
              <button
                className="w-full rounded-full bg-red-600 py-3 text-lg font-bold text-white hover:bg-red-700"
                onClick={() => setStopConfirm(true)}
              >
                EMERGENCY STOP
              </button>
            ) : (
              <div className="space-y-3">
                <p className="text-sm font-medium text-red-300">
                  Are you sure? This will stop ALL trading immediately.
                </p>
                <div className="flex gap-3">
                  <button
                    className="flex-1 rounded-full bg-red-600 py-3 text-sm font-bold text-white hover:bg-red-700 disabled:opacity-50"
                    disabled={stopLoading}
                    onClick={emergencyStop}
                  >
                    {stopLoading ? "Stopping..." : "Yes, Stop Everything"}
                  </button>
                  <button
                    className="flex-1 rounded-full border border-white/20 py-3 text-sm hover:bg-white/10"
                    onClick={() => setStopConfirm(false)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>

        {/* Execution Config Editor */}
        <section className="panel">
          <h3 className="mb-4 text-xl font-semibold">Execution Config</h3>
          {execConfig ? (
            <div className="space-y-4">
              <div className="rounded-2xl bg-black/20 p-4">
                <p className="text-xs text-white/50">live_trading_enabled</p>
                <p className={`mt-1 text-lg font-semibold ${execConfig.live_trading_enabled ? "text-green-400" : "text-red-400"}`}>
                  {execConfig.live_trading_enabled ? "true" : "false"}
                </p>
                <p className="mt-1 text-xs text-white/40">
                  Controlled via Enable Live / Emergency Stop buttons above.
                </p>
              </div>

              <div className="rounded-2xl bg-black/20 p-4">
                <label className="block text-xs text-white/50">allowed_exchanges</label>
                <input
                  type="text"
                  value={editExchanges}
                  onChange={(e) => {
                    setEditExchanges(e.target.value);
                    setConfigDirty(true);
                  }}
                  placeholder="binance, bybit, okx"
                  className="mt-2 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white/90 outline-none focus:border-mint/50"
                />
                <p className="mt-1 text-xs text-white/40">Comma-separated exchange identifiers.</p>
              </div>

              <div className="rounded-2xl bg-black/20 p-4">
                <label className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={editShadow}
                    onChange={(e) => {
                      setEditShadow(e.target.checked);
                      setConfigDirty(true);
                    }}
                    className="h-4 w-4 rounded border-white/20 bg-black/30 accent-mint"
                  />
                  <span className="text-sm">default_shadow_mode</span>
                </label>
                <p className="mt-2 text-xs text-white/40">
                  When enabled, new strategies start in shadow mode by default.
                </p>
              </div>

              <button
                className="rounded-full bg-sand px-6 py-2.5 text-sm font-semibold text-ink hover:bg-sand/80 disabled:opacity-40"
                disabled={!configDirty || configSaving}
                onClick={saveConfig}
              >
                {configSaving ? "Saving..." : "Save Config"}
              </button>
            </div>
          ) : (
            <p className="text-sm text-white/50">Loading execution config...</p>
          )}
        </section>
      </main>
    </AdminGuard>
  );
}
