"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";
import { LiveFeed } from "../../components/live-feed";

interface PortfolioPosition {
  asset: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
}

interface Portfolio {
  positions?: PortfolioPosition[];
  total_exposure?: number;
  cash_balance?: number;
  total_equity?: number;
}

interface Signal {
  asset: string;
  signal_score: number;
  direction: string;
  feature_timestamp?: string;
  components?: Record<string, unknown>;
}

interface Order {
  order_id?: string;
  asset?: string;
  side?: string;
  quantity?: number;
  status?: string;
  created_at?: string;
}

interface DashboardData {
  user?: Record<string, unknown>;
  generated_at?: string;
  active_strategy?: Record<string, unknown> | null;
  active_strategy_error?: string;
  portfolio?: Portfolio | null;
  portfolio_error?: string;
  signals?: Signal[];
  signals_error?: string;
  statistics?: Record<string, unknown> | null;
  statistics_error?: string;
  orders?: Order[];
  orders_error?: string;
  memory_probe?: unknown;
  settings?: {
    execution?: Record<string, unknown>;
    credentials?: unknown[];
  };
}

function formatNumber(value: number | undefined | null, decimals = 2): string {
  if (value == null) return "--";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function ErrorHint({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p className="mt-1 text-xs text-red-400/80 italic">
      Service unavailable: {message.slice(0, 80)}
    </p>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl bg-black/20 p-4">
      <p className="text-xs uppercase tracking-[0.2em] text-mint">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {sub ? <p className="mt-1 text-xs text-white/50">{sub}</p> : null}
    </div>
  );
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    gatewayFetch("/dashboard")
      .then((d) => {
        setData(d as DashboardData);
        setError(null);
      })
      .catch((e) => {
        setData(null);
        setError(e instanceof Error ? e.message : "Failed to load dashboard");
      })
      .finally(() => setLoading(false));
  }, []);

  const portfolio = data?.portfolio ?? null;
  const positions: PortfolioPosition[] =
    (portfolio && Array.isArray(portfolio.positions) ? portfolio.positions : null) ?? [];
  const signals: Signal[] = Array.isArray(data?.signals) ? (data?.signals ?? []) : [];
  const orders: Order[] = Array.isArray(data?.orders) ? (data?.orders ?? []) : [];
  const execution =
    (data?.settings?.execution as Record<string, unknown> | undefined) ?? null;
  const statistics = data?.statistics ?? null;

  if (loading) {
    return (
      <main className="grid gap-6">
        <div className="panel animate-pulse">
          <p className="text-white/60">Loading dashboard...</p>
        </div>
      </main>
    );
  }

  if (error && !data) {
    return (
      <main className="grid gap-6">
        <div className="panel">
          <p className="text-red-400">{error}</p>
          <p className="mt-2 text-sm text-white/60">
            Make sure you are logged in and the gateway is reachable.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="grid gap-6">
      {/* Row 1: Chart + Portfolio summary */}
      <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="panel">
          <h2 className="mb-3 text-2xl font-semibold">Portfolio Dashboard</h2>
          <ChartPlaceholder />
        </div>
        <div className="panel space-y-3">
          <h3 className="mb-3 text-lg font-semibold">Portfolio Summary</h3>
          {data?.portfolio_error ? (
            <ErrorHint message={data.portfolio_error} />
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <StatCard
                label="Total Equity"
                value={`$${formatNumber(portfolio?.total_equity)}`}
              />
              <StatCard
                label="Cash Balance"
                value={`$${formatNumber(portfolio?.cash_balance)}`}
              />
              <StatCard
                label="Total Exposure"
                value={`$${formatNumber(portfolio?.total_exposure)}`}
              />
              <StatCard
                label="Open Positions"
                value={String(positions.length)}
              />
            </div>
          )}

          <div className="rounded-2xl bg-black/20 p-4">
            <p className="text-xs uppercase tracking-[0.2em] text-mint">Active Strategy</p>
            {data?.active_strategy_error ? (
              <ErrorHint message={data.active_strategy_error} />
            ) : (
              <pre className="mt-2 overflow-x-auto text-xs">
                {JSON.stringify(data?.active_strategy ?? null, null, 2)}
              </pre>
            )}
          </div>

          {execution ? (
            <div className="rounded-2xl bg-black/20 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-mint">Execution Posture</p>
              <pre className="mt-2 overflow-x-auto text-xs">
                {JSON.stringify(execution, null, 2)}
              </pre>
            </div>
          ) : null}
        </div>
      </section>

      {/* Row 2: Positions, Signals, Orders */}
      <section className="grid gap-6 lg:grid-cols-3">
        {/* Positions */}
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Positions</h3>
          {positions.length === 0 ? (
            <p className="text-sm text-white/50">No open positions</p>
          ) : (
            <div className="space-y-2">
              {positions.map((pos) => (
                <div
                  key={pos.asset}
                  className="flex items-center justify-between rounded-xl bg-black/20 p-3 text-sm"
                >
                  <div>
                    <p className="font-medium">{pos.asset}</p>
                    <p className="text-xs text-white/50">
                      Qty {formatNumber(pos.quantity, 4)} @ ${formatNumber(pos.entry_price)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-medium">${formatNumber(pos.current_price)}</p>
                    <p
                      className={`text-xs ${
                        (pos.unrealized_pnl ?? 0) >= 0
                          ? "text-green-400"
                          : "text-red-400"
                      }`}
                    >
                      {(pos.unrealized_pnl ?? 0) >= 0 ? "+" : ""}
                      ${formatNumber(pos.unrealized_pnl)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recent Signals */}
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Recent Signals</h3>
          {data?.signals_error ? (
            <ErrorHint message={data.signals_error} />
          ) : signals.length === 0 ? (
            <p className="text-sm text-white/50">No signals available</p>
          ) : (
            <div className="space-y-2">
              {signals.slice(0, 5).map((sig, idx) => (
                <div
                  key={`${sig.asset}-${sig.feature_timestamp ?? idx}`}
                  className="rounded-xl bg-black/20 p-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs uppercase tracking-wider text-mint">
                      {sig.asset}
                    </span>
                    <span className="text-xs text-white/50">{sig.direction}</span>
                  </div>
                  <p className="mt-1 text-xl font-semibold">
                    {formatNumber(sig.signal_score, 4)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Latest Orders */}
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Latest Orders</h3>
          {data?.orders_error ? (
            <ErrorHint message={data.orders_error} />
          ) : orders.length === 0 ? (
            <p className="text-sm text-white/50">No recent orders</p>
          ) : (
            <div className="space-y-2">
              {orders.slice(0, 5).map((order, idx) => (
                <div
                  key={order.order_id ?? idx}
                  className="rounded-xl bg-black/20 p-3 text-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{order.asset ?? "N/A"}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        order.side === "BUY"
                          ? "bg-green-500/20 text-green-400"
                          : "bg-red-500/20 text-red-400"
                      }`}
                    >
                      {order.side ?? "--"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-white/50">
                    Qty: {order.quantity ?? "--"} | Status: {order.status ?? "--"}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Row 3: Statistics */}
      <section className="grid gap-6 lg:grid-cols-2">
        <div className="panel">
          <h3 className="mb-3 text-lg font-semibold">Statistics</h3>
          {data?.statistics_error ? (
            <ErrorHint message={data.statistics_error} />
          ) : statistics ? (
            <pre className="overflow-x-auto text-xs text-white/80">
              {JSON.stringify(statistics, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-white/50">No statistics available</p>
          )}
        </div>
        <LiveFeed />
      </section>
    </main>
  );
}
