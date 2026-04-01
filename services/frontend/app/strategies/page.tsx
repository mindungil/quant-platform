"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";

interface Strategy {
  strategy_id: string;
  name: string;
  asset_type: string;
  status: "DRAFT" | "ACTIVE" | "PAUSED" | "ARCHIVED";
  indicators: string[];
  weights?: Record<string, number>;
  thresholds?: { entry?: number; exit?: number };
  version?: number;
  backtest_id?: string;
  backtest_result?: string;
  created_at?: string;
  updated_at?: string;
}

interface BacktestResult {
  backtest_id: string;
  status: "PENDING" | "RUNNING" | "PASSED" | "FAILED";
  sharpe_ratio?: number;
  max_drawdown?: number;
  total_return?: number;
  trades?: number;
}

type ModalView = "none" | "new" | "backtest";

const ASSET_TYPES = ["crypto", "stock", "forex", "commodity"];
const INDICATOR_OPTIONS = [
  "RSI", "MACD", "EMA", "SMA", "BBANDS", "ATR", "VWAP",
  "OBV", "STOCH", "ADX", "CCI", "MFI", "WILLR", "ICHIMOKU",
];

function statusBadgeClass(status: string): string {
  const s = status.toUpperCase();
  if (s === "ACTIVE") return "bg-green-50 text-green-700";
  if (s === "PAUSED") return "bg-yellow-50 text-yellow-700";
  if (s === "ARCHIVED") return "bg-neutral-100 text-neutral-400";
  return "bg-neutral-100 text-neutral-500"; // DRAFT
}

function formatNumber(value: number | undefined | null, decimals = 2): string {
  if (value == null) return "--";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function StrategiesContent() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalView>("none");
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // New strategy form state
  const [newName, setNewName] = useState("");
  const [newAssetType, setNewAssetType] = useState("crypto");
  const [newIndicators, setNewIndicators] = useState<Set<string>>(new Set());
  const [newWeights, setNewWeights] = useState<Record<string, number>>({});
  const [newEntryThreshold, setNewEntryThreshold] = useState(0.6);
  const [newExitThreshold, setNewExitThreshold] = useState(0.3);
  const [formError, setFormError] = useState<string | null>(null);

  // Backtest state
  const [backtestStrategyId, setBacktestStrategyId] = useState<string | null>(null);
  const [backtestResult, setBacktestResult] = useState<BacktestResult | null>(null);
  const [backtestPolling, setBacktestPolling] = useState(false);

  const fetchStrategies = useCallback(() => {
    setLoading(true);
    gatewayFetch("/strategies")
      .then((data) => {
        const items = Array.isArray(data) ? data : (data as { strategies?: Strategy[] }).strategies ?? [];
        setStrategies(items as Strategy[]);
        setError(null);
      })
      .catch((e) => {
        setStrategies([]);
        setError(e instanceof Error ? e.message : "Failed to load strategies");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchStrategies();
  }, [fetchStrategies]);

  function toggleIndicator(ind: string) {
    setNewIndicators((prev) => {
      const next = new Set(prev);
      if (next.has(ind)) {
        next.delete(ind);
        setNewWeights((w) => {
          const copy = { ...w };
          delete copy[ind];
          return copy;
        });
      } else {
        next.add(ind);
        setNewWeights((w) => ({ ...w, [ind]: 1.0 }));
      }
      return next;
    });
  }

  function setWeight(ind: string, value: number) {
    setNewWeights((w) => ({ ...w, [ind]: value }));
  }

  async function createStrategy() {
    if (!newName.trim()) {
      setFormError("Name is required");
      return;
    }
    if (newIndicators.size === 0) {
      setFormError("Select at least one indicator");
      return;
    }
    setFormError(null);
    setActionLoading("create");
    try {
      await gatewayFetch("/strategies", {
        method: "POST",
        body: JSON.stringify({
          name: newName.trim(),
          asset_type: newAssetType,
          indicators: Array.from(newIndicators),
          weights: newWeights,
          thresholds: { entry: newEntryThreshold, exit: newExitThreshold },
        }),
      });
      setModal("none");
      setNewName("");
      setNewIndicators(new Set());
      setNewWeights({});
      setNewEntryThreshold(0.6);
      setNewExitThreshold(0.3);
      fetchStrategies();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Failed to create strategy");
    } finally {
      setActionLoading(null);
    }
  }

  async function runBacktest(strategyId: string) {
    setBacktestStrategyId(strategyId);
    setBacktestResult(null);
    setModal("backtest");
    setBacktestPolling(true);

    try {
      const result = await gatewayFetch("/backtests", {
        method: "POST",
        body: JSON.stringify({ strategy_id: strategyId }),
      }) as BacktestResult;

      setBacktestResult(result);

      // Poll for completion if still running
      if (result.status === "PENDING" || result.status === "RUNNING") {
        let attempts = 0;
        const maxAttempts = 30;
        const poll = async () => {
          if (attempts >= maxAttempts) {
            setBacktestPolling(false);
            return;
          }
          attempts++;
          try {
            const updated = await gatewayFetch(`/backtests/${result.backtest_id}`) as BacktestResult;
            setBacktestResult(updated);
            if (updated.status === "PENDING" || updated.status === "RUNNING") {
              setTimeout(poll, 2000);
            } else {
              setBacktestPolling(false);
              fetchStrategies();
            }
          } catch {
            setBacktestPolling(false);
          }
        };
        setTimeout(poll, 2000);
      } else {
        setBacktestPolling(false);
        fetchStrategies();
      }
    } catch (e) {
      setBacktestResult(null);
      setBacktestPolling(false);
      alert(e instanceof Error ? e.message : "Failed to start backtest");
      setModal("none");
    }
  }

  async function activateStrategy(strategyId: string) {
    setActionLoading(strategyId);
    try {
      await gatewayFetch(`/strategies/${strategyId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "ACTIVE" }),
      });
      fetchStrategies();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to activate strategy");
    } finally {
      setActionLoading(null);
    }
  }

  async function pauseStrategy(strategyId: string) {
    setActionLoading(strategyId);
    try {
      await gatewayFetch(`/strategies/${strategyId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "PAUSED" }),
      });
      fetchStrategies();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to pause strategy");
    } finally {
      setActionLoading(null);
    }
  }

  async function resumeStrategy(strategyId: string) {
    setActionLoading(strategyId);
    try {
      await gatewayFetch(`/strategies/${strategyId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "ACTIVE" }),
      });
      fetchStrategies();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to resume strategy");
    } finally {
      setActionLoading(null);
    }
  }

  async function archiveStrategy(strategyId: string) {
    setActionLoading(strategyId);
    try {
      await gatewayFetch(`/strategies/${strategyId}`, {
        method: "PATCH",
        body: JSON.stringify({ status: "ARCHIVED" }),
      });
      fetchStrategies();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to archive strategy");
    } finally {
      setActionLoading(null);
    }
  }

  const canActivate = (s: Strategy) =>
    s.status === "DRAFT" && (s.backtest_result === "PASSED" || s.backtest_id != null);

  return (
    <main className="grid gap-6">
      {/* Header */}
      <section className="card">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-2xl font-semibold text-neutral-900">Strategies</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setModal("new")}
              className="btn-primary"
            >
              + New Strategy
            </button>
            <button
              onClick={fetchStrategies}
              className="btn-secondary"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      {/* New Strategy Modal */}
      {modal === "new" && (
        <section className="card border-neutral-300">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-neutral-900">Create New Strategy</h3>
            <button
              onClick={() => setModal("none")}
              className="text-sm text-neutral-400 hover:text-neutral-900"
            >
              Close
            </button>
          </div>

          <div className="mt-4 space-y-4">
            {formError && (
              <p className="rounded-lg bg-red-50 p-3 text-sm text-red-600">{formError}</p>
            )}

            <div>
              <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">Strategy Name</label>
              <input
                className="input-field"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="My Momentum Strategy"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">Asset Type</label>
              <select
                className="input-field"
                value={newAssetType}
                onChange={(e) => setNewAssetType(e.target.value)}
              >
                {ASSET_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.charAt(0).toUpperCase() + t.slice(1)}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">Indicators</label>
              <div className="mt-2 flex flex-wrap gap-2">
                {INDICATOR_OPTIONS.map((ind) => (
                  <button
                    key={ind}
                    onClick={() => toggleIndicator(ind)}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                      newIndicators.has(ind)
                        ? "border-neutral-900 bg-neutral-900 text-white"
                        : "border-neutral-200 text-neutral-500 hover:border-neutral-400"
                    }`}
                  >
                    {ind}
                  </button>
                ))}
              </div>
            </div>

            {newIndicators.size > 0 && (
              <div>
                <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">Weights</label>
                <div className="mt-2 space-y-2">
                  {Array.from(newIndicators).map((ind) => (
                    <div key={ind} className="flex items-center gap-3">
                      <span className="w-20 text-xs text-neutral-600">{ind}</span>
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={newWeights[ind] ?? 1.0}
                        onChange={(e) => setWeight(ind, parseFloat(e.target.value))}
                        className="flex-1 accent-neutral-900"
                      />
                      <span className="w-10 text-right text-xs text-neutral-400">
                        {(newWeights[ind] ?? 1.0).toFixed(1)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">Entry Threshold</label>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={newEntryThreshold}
                    onChange={(e) => setNewEntryThreshold(parseFloat(e.target.value))}
                    className="flex-1 accent-neutral-900"
                  />
                  <span className="w-10 text-right text-sm text-neutral-400">
                    {newEntryThreshold.toFixed(2)}
                  </span>
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">Exit Threshold</label>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={newExitThreshold}
                    onChange={(e) => setNewExitThreshold(parseFloat(e.target.value))}
                    className="flex-1 accent-neutral-900"
                  />
                  <span className="w-10 text-right text-sm text-neutral-400">
                    {newExitThreshold.toFixed(2)}
                  </span>
                </div>
              </div>
            </div>

            <button
              onClick={createStrategy}
              disabled={actionLoading === "create"}
              className="btn-primary disabled:opacity-40"
            >
              {actionLoading === "create" ? "Creating..." : "Create Strategy"}
            </button>
          </div>
        </section>
      )}

      {/* Backtest Modal */}
      {modal === "backtest" && (
        <section className="card border-neutral-300">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-neutral-900">Backtest Results</h3>
            <button
              onClick={() => { setModal("none"); setBacktestResult(null); }}
              className="text-sm text-neutral-400 hover:text-neutral-900"
            >
              Close
            </button>
          </div>

          {backtestPolling && !backtestResult && (
            <div className="mt-4 animate-pulse">
              <p className="text-neutral-400">Starting backtest...</p>
            </div>
          )}

          {backtestResult && (
            <div className="mt-4 space-y-3">
              <div className="flex items-center gap-3">
                <span className={`badge ${
                  backtestResult.status === "PASSED" ? "bg-green-50 text-green-700" :
                  backtestResult.status === "FAILED" ? "bg-red-50 text-red-700" :
                  "bg-yellow-50 text-yellow-700"
                }`}>
                  {backtestResult.status}
                </span>
                {backtestPolling && <span className="text-xs text-neutral-400 animate-pulse">Polling...</span>}
              </div>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                  <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Sharpe Ratio</p>
                  <p className="mt-1 text-lg font-semibold text-neutral-900">{formatNumber(backtestResult.sharpe_ratio)}</p>
                </div>
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                  <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Max Drawdown</p>
                  <p className="mt-1 text-lg font-semibold text-neutral-900">
                    {backtestResult.max_drawdown != null ? `${(backtestResult.max_drawdown * 100).toFixed(1)}%` : "--"}
                  </p>
                </div>
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                  <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Total Return</p>
                  <p className="mt-1 text-lg font-semibold text-neutral-900">
                    {backtestResult.total_return != null ? `${(backtestResult.total_return * 100).toFixed(1)}%` : "--"}
                  </p>
                </div>
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                  <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Trades</p>
                  <p className="mt-1 text-lg font-semibold text-neutral-900">{backtestResult.trades ?? "--"}</p>
                </div>
              </div>

              {backtestResult.status === "PASSED" && backtestStrategyId && (
                <button
                  onClick={() => {
                    activateStrategy(backtestStrategyId);
                    setModal("none");
                    setBacktestResult(null);
                  }}
                  className="rounded-lg bg-green-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-green-700"
                >
                  Activate Strategy
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {/* Strategy List */}
      {loading ? (
        <div className="card animate-pulse">
          <p className="text-neutral-400">Loading strategies...</p>
        </div>
      ) : error ? (
        <div className="card">
          <p className="text-red-500">{error}</p>
          <p className="mt-2 text-sm text-neutral-500">
            Make sure you are logged in and the strategy service is running.
          </p>
        </div>
      ) : strategies.length === 0 ? (
        <div className="card">
          <p className="text-neutral-400">No strategies yet. Create one to get started.</p>
        </div>
      ) : (
        <section className="grid gap-4 md:grid-cols-2">
          {strategies.map((strategy) => {
            const isLoading = actionLoading === strategy.strategy_id;
            return (
              <article key={strategy.strategy_id} className="card">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-neutral-900">{strategy.name}</h3>
                  <span className={`badge ${statusBadgeClass(strategy.status)}`}>
                    {strategy.status}
                  </span>
                </div>

                <div className="mt-3 space-y-2 text-sm text-neutral-600">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium uppercase tracking-wider text-neutral-400">Asset</span>
                    <span>{strategy.asset_type}</span>
                  </div>

                  {strategy.indicators && strategy.indicators.length > 0 && (
                    <div>
                      <span className="text-xs font-medium uppercase tracking-wider text-neutral-400">Indicators</span>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {strategy.indicators.map((ind) => (
                          <span key={ind} className="rounded-md bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
                            {ind}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {strategy.thresholds && (
                    <div className="flex gap-4">
                      <span className="text-xs">
                        Entry: <span className="font-medium text-neutral-900">{strategy.thresholds.entry ?? "--"}</span>
                      </span>
                      <span className="text-xs">
                        Exit: <span className="font-medium text-neutral-900">{strategy.thresholds.exit ?? "--"}</span>
                      </span>
                    </div>
                  )}

                  {strategy.version != null && (
                    <p className="text-xs text-neutral-400">Version: {strategy.version}</p>
                  )}
                </div>

                {/* Action buttons */}
                <div className="mt-4 flex flex-wrap gap-2">
                  {strategy.status === "DRAFT" && (
                    <>
                      <button
                        onClick={() => runBacktest(strategy.strategy_id)}
                        disabled={isLoading}
                        className="btn-secondary text-xs disabled:opacity-40"
                      >
                        Run Backtest
                      </button>
                      {canActivate(strategy) && (
                        <button
                          onClick={() => activateStrategy(strategy.strategy_id)}
                          disabled={isLoading}
                          className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-40"
                        >
                          {isLoading ? "Activating..." : "Activate"}
                        </button>
                      )}
                    </>
                  )}
                  {strategy.status === "ACTIVE" && (
                    <button
                      onClick={() => pauseStrategy(strategy.strategy_id)}
                      disabled={isLoading}
                      className="rounded-lg border border-yellow-300 px-3 py-1.5 text-xs font-medium text-yellow-700 hover:bg-yellow-50 disabled:opacity-40"
                    >
                      {isLoading ? "Pausing..." : "Pause"}
                    </button>
                  )}
                  {strategy.status === "PAUSED" && (
                    <button
                      onClick={() => resumeStrategy(strategy.strategy_id)}
                      disabled={isLoading}
                      className="rounded-lg border border-green-300 px-3 py-1.5 text-xs font-medium text-green-700 hover:bg-green-50 disabled:opacity-40"
                    >
                      {isLoading ? "Resuming..." : "Resume"}
                    </button>
                  )}
                  {strategy.status !== "ARCHIVED" && (
                    <button
                      onClick={() => archiveStrategy(strategy.strategy_id)}
                      disabled={isLoading}
                      className="rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-400 hover:bg-neutral-50 disabled:opacity-40"
                    >
                      Archive
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}

export default function StrategiesPage() {
  return (
    <AuthGuard>
      <StrategiesContent />
    </AuthGuard>
  );
}
