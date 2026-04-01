"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";
import { LiveFeed } from "../../components/live-feed";
import { AuthGuard } from "../../components/auth-guard";
import { connectGatewaySocket } from "../../lib/socket";

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
    <p className="mt-1 text-xs italic text-red-500">
      Service unavailable: {message.slice(0, 80)}
    </p>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-neutral-900">{value}</p>
      {sub ? <p className="mt-1 text-xs text-neutral-400">{sub}</p> : null}
    </div>
  );
}

function WsIndicator() {
  const [connected, setConnected] = useState(false);
  const [msgCount, setMsgCount] = useState(0);

  useEffect(() => {
    let alive = true;
    const disconnect = connectGatewaySocket((payload) => {
      if (alive) {
        setConnected(true);
        setMsgCount((c) => c + 1);
      }
      void payload;
    });

    // Assume connected after short delay if no error
    const timer = window.setTimeout(() => {
      if (alive) setConnected(true);
    }, 3000);

    return () => {
      alive = false;
      clearTimeout(timer);
      disconnect();
      setConnected(false);
    };
  }, []);

  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className={`inline-block h-2 w-2 rounded-full ${
          connected ? "bg-green-500" : "bg-red-500 animate-pulse"
        }`}
      />
      <span className="text-neutral-400">
        {connected ? `Live feed connected (${msgCount} msgs)` : "Connecting..."}
      </span>
    </div>
  );
}

function DashboardContent() {
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
        <div className="card animate-pulse">
          <p className="text-neutral-400">Loading dashboard...</p>
        </div>
      </main>
    );
  }

  if (error && !data) {
    return (
      <main className="grid gap-6">
        <div className="card">
          <p className="text-red-500">{error}</p>
          <p className="mt-2 text-sm text-neutral-500">
            Make sure you are logged in and the gateway is reachable.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="grid gap-6">
      {/* WebSocket connection indicator */}
      <div className="flex items-center justify-end">
        <WsIndicator />
      </div>

      {/* Row 1: Chart + Portfolio summary */}
      <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="card">
          <h2 className="mb-3 text-2xl font-semibold text-neutral-900">Portfolio Dashboard</h2>
          <ChartPlaceholder />
        </div>
        <div className="card space-y-3">
          <h3 className="mb-3 text-lg font-semibold text-neutral-900">Portfolio Summary</h3>
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

          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">Active Strategy</p>
            {data?.active_strategy_error ? (
              <ErrorHint message={data.active_strategy_error} />
            ) : (
              <pre className="mt-2 overflow-x-auto text-xs text-neutral-600">
                {JSON.stringify(data?.active_strategy ?? null, null, 2)}
              </pre>
            )}
          </div>

          {execution ? (
            <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
              <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">Execution Posture</p>
              <pre className="mt-2 overflow-x-auto text-xs text-neutral-600">
                {JSON.stringify(execution, null, 2)}
              </pre>
            </div>
          ) : null}
        </div>
      </section>

      {/* Row 2: Positions, Signals, Orders */}
      <section className="grid gap-6 lg:grid-cols-3">
        {/* Positions */}
        <div className="card">
          <h3 className="mb-3 text-lg font-semibold text-neutral-900">Positions</h3>
          {positions.length === 0 ? (
            <p className="text-sm text-neutral-400">No open positions</p>
          ) : (
            <div className="space-y-2">
              {positions.map((pos) => (
                <div
                  key={pos.asset}
                  className="flex items-center justify-between rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-sm"
                >
                  <div>
                    <p className="font-medium text-neutral-900">{pos.asset}</p>
                    <p className="text-xs text-neutral-400">
                      Qty {formatNumber(pos.quantity, 4)} @ ${formatNumber(pos.entry_price)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-medium text-neutral-900">${formatNumber(pos.current_price)}</p>
                    <p
                      className={`text-xs ${
                        (pos.unrealized_pnl ?? 0) >= 0
                          ? "text-green-600"
                          : "text-red-600"
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
        <div className="card">
          <h3 className="mb-3 text-lg font-semibold text-neutral-900">Recent Signals</h3>
          {data?.signals_error ? (
            <ErrorHint message={data.signals_error} />
          ) : signals.length === 0 ? (
            <p className="text-sm text-neutral-400">No signals available</p>
          ) : (
            <div className="space-y-2">
              {signals.slice(0, 5).map((sig, idx) => (
                <div
                  key={`${sig.asset}-${sig.feature_timestamp ?? idx}`}
                  className="rounded-lg border border-neutral-100 bg-neutral-50 p-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium uppercase tracking-wider text-neutral-500">
                      {sig.asset}
                    </span>
                    <span className={`badge ${
                      sig.direction.toUpperCase() === "BUY" || sig.direction.toUpperCase() === "LONG"
                        ? "bg-green-50 text-green-700"
                        : sig.direction.toUpperCase() === "SELL" || sig.direction.toUpperCase() === "SHORT"
                          ? "bg-red-50 text-red-700"
                          : "bg-neutral-100 text-neutral-500"
                    }`}>
                      {sig.direction}
                    </span>
                  </div>
                  <p className="mt-1 text-xl font-semibold text-neutral-900">
                    {formatNumber(sig.signal_score, 4)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Latest Orders */}
        <div className="card">
          <h3 className="mb-3 text-lg font-semibold text-neutral-900">Latest Orders</h3>
          {data?.orders_error ? (
            <ErrorHint message={data.orders_error} />
          ) : orders.length === 0 ? (
            <p className="text-sm text-neutral-400">No recent orders</p>
          ) : (
            <div className="space-y-2">
              {orders.slice(0, 5).map((order, idx) => (
                <div
                  key={order.order_id ?? idx}
                  className="rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-neutral-900">{order.asset ?? "N/A"}</span>
                    <span
                      className={`badge ${
                        order.side === "BUY"
                          ? "bg-green-50 text-green-700"
                          : "bg-red-50 text-red-700"
                      }`}
                    >
                      {order.side ?? "--"}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-neutral-400">
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
        <div className="card">
          <h3 className="mb-3 text-lg font-semibold text-neutral-900">Statistics</h3>
          {data?.statistics_error ? (
            <ErrorHint message={data.statistics_error} />
          ) : statistics ? (
            <pre className="overflow-x-auto text-xs text-neutral-600">
              {JSON.stringify(statistics, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-neutral-400">No statistics available</p>
          )}
        </div>
        <LiveFeed />
      </section>
    </main>
  );
}

export default function DashboardPage() {
  return (
    <AuthGuard>
      <DashboardContent />
    </AuthGuard>
  );
}
