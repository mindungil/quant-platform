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
  if (s === "ACTIVE") return "bg-green-500/20 text-green-400";
  if (s === "PAUSED") return "bg-sand/20 text-sand";
  if (s === "ARCHIVED") return "bg-white/10 text-white/40";
  return "bg-mint/20 text-mint"; // DRAFT
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
      <section className="panel">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-2xl font-semibold">Strategies</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setModal("new")}
              className="rounded-full bg-sand px-4 py-2 text-sm font-medium text-ink hover:bg-sand/90"
            >
              + New Strategy
            </button>
            <button
              onClick={fetchStrategies}
              className="rounded-full border border-white/10 px-3 py-1 text-xs text-white/60 hover:bg-white/10"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      {/* New Strategy Modal */}
      {modal === "new" && (
        <section className="panel border-sand/30">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">Create New Strategy</h3>
            <button
              onClick={() => setModal("none")}
              className="text-sm text-white/50 hover:text-white"
            >
              Close
            </button>
          </div>

          <div className="mt-4 space-y-4">
            {formError && (
              <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-400">{formError}</p>
            )}

            <div>
              <label className="text-xs uppercase tracking-wider text-mint">Strategy Name</label>
              <input
                className="mt-1 w-full rounded-2xl bg-white/10 px-4 py-3 text-sm"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="My Momentum Strategy"
              />
            </div>

            <div>
              <label className="text-xs uppercase tracking-wider text-mint">Asset Type</label>
              <select
                className="mt-1 w-full rounded-2xl bg-white/10 px-4 py-3 text-sm"
                value={newAssetType}
                onChange={(e) => setNewAssetType(e.target.value)}
              >
                {ASSET_TYPES.map((t) => (
                  <option key={t} value={t} className="bg-ink">
                    {t.charAt(0).toUpperCase() + t.slice(1)}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs uppercase tracking-wider text-mint">Indicators</label>
              <div className="mt-2 flex flex-wrap gap-2">
                {INDICATOR_OPTIONS.map((ind) => (
                  <button
                    key={ind}
                    onClick={() => toggleIndicator(ind)}
                    className={`rounded-full border px-3 py-1 text-xs transition ${
                      newIndicators.has(ind)
                        ? "border-sand bg-sand/20 text-sand"
                        : "border-white/10 text-white/60 hover:bg-white/10"
                    }`}
                  >
                    {ind}
                  </button>
                ))}
              </div>
            </div>

            {newIndicators.size > 0 && (
              <div>
                <label className="text-xs uppercase tracking-wider text-mint">Weights</label>
                <div className="mt-2 space-y-2">
                  {Array.from(newIndicators).map((ind) => (
                    <div key={ind} className="flex items-center gap-3">
                      <span className="w-20 text-xs text-white/70">{ind}</span>
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={newWeights[ind] ?? 1.0}
                        onChange={(e) => setWeight(ind, parseFloat(e.target.value))}
                        className="flex-1"
                      />
                      <span className="w-10 text-right text-xs text-white/60">
                        {(newWeights[ind] ?? 1.0).toFixed(1)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs uppercase tracking-wider text-mint">Entry Threshold</label>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={newEntryThreshold}
                    onChange={(e) => setNewEntryThreshold(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <span className="w-10 text-right text-sm text-white/60">
                    {newEntryThreshold.toFixed(2)}
                  </span>
                </div>
              </div>
              <div>
                <label className="text-xs uppercase tracking-wider text-mint">Exit Threshold</label>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={newExitThreshold}
                    onChange={(e) => setNewExitThreshold(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <span className="w-10 text-right text-sm text-white/60">
                    {newExitThreshold.toFixed(2)}
                  </span>
                </div>
              </div>
            </div>

            <button
              onClick={createStrategy}
              disabled={actionLoading === "create"}
              className="rounded-full bg-sand px-6 py-2 text-sm font-medium text-ink hover:bg-sand/90 disabled:opacity-40"
            >
              {actionLoading === "create" ? "Creating..." : "Create Strategy"}
            </button>
          </div>
        </section>
      )}

      {/* Backtest Modal */}
      {modal === "backtest" && (
        <section className="panel border-mint/30">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">Backtest Results</h3>
            <button
              onClick={() => { setModal("none"); setBacktestResult(null); }}
              className="text-sm text-white/50 hover:text-white"
            >
              Close
            </button>
          </div>

          {backtestPolling && !backtestResult && (
            <div className="mt-4 animate-pulse">
              <p className="text-white/60">Starting backtest...</p>
            </div>
          )}

          {backtestResult && (
            <div className="mt-4 space-y-3">
              <div className="flex items-center gap-3">
                <span className={`rounded-full px-3 py-1 text-xs font-medium ${
                  backtestResult.status === "PASSED" ? "bg-green-500/20 text-green-400" :
                  backtestResult.status === "FAILED" ? "bg-red-500/20 text-red-400" :
                  "bg-sand/20 text-sand"
                }`}>
                  {backtestResult.status}
                </span>
                {backtestPolling && <span className="text-xs text-white/50 animate-pulse">Polling...</span>}
              </div>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <div className="rounded-xl bg-black/20 p-3">
                  <p className="text-xs uppercase tracking-wider text-mint">Sharpe Ratio</p>
                  <p className="mt-1 text-lg font-semibold">{formatNumber(backtestResult.sharpe_ratio)}</p>
                </div>
                <div className="rounded-xl bg-black/20 p-3">
                  <p className="text-xs uppercase tracking-wider text-mint">Max Drawdown</p>
                  <p className="mt-1 text-lg font-semibold">
                    {backtestResult.max_drawdown != null ? `${(backtestResult.max_drawdown * 100).toFixed(1)}%` : "--"}
                  </p>
                </div>
                <div className="rounded-xl bg-black/20 p-3">
                  <p className="text-xs uppercase tracking-wider text-mint">Total Return</p>
                  <p className="mt-1 text-lg font-semibold">
                    {backtestResult.total_return != null ? `${(backtestResult.total_return * 100).toFixed(1)}%` : "--"}
                  </p>
                </div>
                <div className="rounded-xl bg-black/20 p-3">
                  <p className="text-xs uppercase tracking-wider text-mint">Trades</p>
                  <p className="mt-1 text-lg font-semibold">{backtestResult.trades ?? "--"}</p>
                </div>
              </div>

              {backtestResult.status === "PASSED" && backtestStrategyId && (
                <button
                  onClick={() => {
                    activateStrategy(backtestStrategyId);
                    setModal("none");
                    setBacktestResult(null);
                  }}
                  className="rounded-full bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-500"
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
        <div className="panel animate-pulse">
          <p className="text-white/60">Loading strategies...</p>
        </div>
      ) : error ? (
        <div className="panel">
          <p className="text-red-400">{error}</p>
          <p className="mt-2 text-sm text-white/60">
            Make sure you are logged in and the strategy service is running.
          </p>
        </div>
      ) : strategies.length === 0 ? (
        <div className="panel">
          <p className="text-white/50">No strategies yet. Create one to get started.</p>
        </div>
      ) : (
        <section className="grid gap-4 md:grid-cols-2">
          {strategies.map((strategy) => {
            const isLoading = actionLoading === strategy.strategy_id;
            return (
              <article key={strategy.strategy_id} className="panel">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold">{strategy.name}</h3>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(strategy.status)}`}>
                    {strategy.status}
                  </span>
                </div>

                <div className="mt-3 space-y-2 text-sm text-white/70">
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-wider text-mint">Asset</span>
                    <span>{strategy.asset_type}</span>
                  </div>

                  {strategy.indicators && strategy.indicators.length > 0 && (
                    <div>
                      <span className="text-xs uppercase tracking-wider text-mint">Indicators</span>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {strategy.indicators.map((ind) => (
                          <span key={ind} className="rounded-full bg-white/10 px-2 py-0.5 text-xs">
                            {ind}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {strategy.thresholds && (
                    <div className="flex gap-4">
                      <span className="text-xs">
                        Entry: <span className="text-white">{strategy.thresholds.entry ?? "--"}</span>
                      </span>
                      <span className="text-xs">
                        Exit: <span className="text-white">{strategy.thresholds.exit ?? "--"}</span>
                      </span>
                    </div>
                  )}

                  {strategy.version != null && (
                    <p className="text-xs text-white/40">Version: {strategy.version}</p>
                  )}
                </div>

                {/* Action buttons */}
                <div className="mt-4 flex flex-wrap gap-2">
                  {strategy.status === "DRAFT" && (
                    <>
                      <button
                        onClick={() => runBacktest(strategy.strategy_id)}
                        disabled={isLoading}
                        className="rounded-full border border-mint/30 px-3 py-1 text-xs text-mint hover:bg-mint/10 disabled:opacity-40"
                      >
                        Run Backtest
                      </button>
                      {canActivate(strategy) && (
                        <button
                          onClick={() => activateStrategy(strategy.strategy_id)}
                          disabled={isLoading}
                          className="rounded-full bg-green-600 px-3 py-1 text-xs text-white hover:bg-green-500 disabled:opacity-40"
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
                      className="rounded-full border border-sand/30 px-3 py-1 text-xs text-sand hover:bg-sand/10 disabled:opacity-40"
                    >
                      {isLoading ? "Pausing..." : "Pause"}
                    </button>
                  )}
                  {strategy.status === "PAUSED" && (
                    <button
                      onClick={() => resumeStrategy(strategy.strategy_id)}
                      disabled={isLoading}
                      className="rounded-full border border-green-400/30 px-3 py-1 text-xs text-green-400 hover:bg-green-500/10 disabled:opacity-40"
                    >
                      {isLoading ? "Resuming..." : "Resume"}
                    </button>
                  )}
                  {strategy.status !== "ARCHIVED" && (
                    <button
                      onClick={() => archiveStrategy(strategy.strategy_id)}
                      disabled={isLoading}
                      className="rounded-full border border-white/10 px-3 py-1 text-xs text-white/40 hover:bg-white/10 disabled:opacity-40"
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
