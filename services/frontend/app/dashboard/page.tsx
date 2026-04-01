"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import { ChartPlaceholder } from "../../components/chart-placeholder";
import { LiveFeed } from "../../components/live-feed";
import { AuthGuard } from "../../components/auth-guard";
import { connectGatewaySocket } from "../../lib/socket";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  AnimatedNumber,
  FadeInView,
} from "../../components/motion";

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

function StatCard({ label, value, rawValue, prefix, decimals = 2, sub }: { label: string; value: string; rawValue?: number; prefix?: string; decimals?: number; sub?: string }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-neutral-900">
        {rawValue != null ? (
          <>{prefix}<AnimatedNumber value={rawValue} decimals={decimals} /></>
        ) : (
          <span className="stat-value">{value}</span>
        )}
      </p>
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
      {connected ? (
        <span className="live-dot" />
      ) : (
        <span className="inline-block h-2 w-2 rounded-full bg-red-500 animate-pulse" />
      )}
      <span className="text-neutral-400">
        {connected ? `Live feed connected (${msgCount} msgs)` : "Connecting..."}
      </span>
    </div>
  );
}

function SkeletonDashboard() {
  return (
    <main className="grid gap-6">
      <div className="flex items-center justify-end">
        <div className="skeleton h-4 w-48" />
      </div>
      <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="card space-y-4">
          <div className="skeleton h-6 w-48" />
          <div className="skeleton h-48 w-full" />
        </div>
        <div className="card space-y-3">
          <div className="skeleton h-6 w-40" />
          <div className="grid grid-cols-2 gap-3">
            <div className="skeleton h-20 w-full" />
            <div className="skeleton h-20 w-full" />
            <div className="skeleton h-20 w-full" />
            <div className="skeleton h-20 w-full" />
          </div>
          <div className="skeleton h-24 w-full" />
        </div>
      </section>
      <section className="grid gap-6 lg:grid-cols-3">
        <div className="card space-y-3">
          <div className="skeleton h-6 w-24" />
          <div className="skeleton h-16 w-full" />
          <div className="skeleton h-16 w-full" />
        </div>
        <div className="card space-y-3">
          <div className="skeleton h-6 w-32" />
          <div className="skeleton h-16 w-full" />
          <div className="skeleton h-16 w-full" />
        </div>
        <div className="card space-y-3">
          <div className="skeleton h-6 w-28" />
          <div className="skeleton h-16 w-full" />
          <div className="skeleton h-16 w-full" />
        </div>
      </section>
    </main>
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
    return <SkeletonDashboard />;
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
    <PageTransition>
      <main className="grid gap-6">
        {/* WebSocket connection indicator */}
        <div className="flex items-center justify-end">
          <WsIndicator />
        </div>

        {/* Row 1: Chart + Portfolio summary */}
        <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <FadeInView>
            <div className="card">
              <h2 className="mb-3 text-2xl font-semibold text-neutral-900">Portfolio Dashboard</h2>
              <ChartPlaceholder />
            </div>
          </FadeInView>
          <FadeInView delay={0.1}>
            <div className="card space-y-3">
              <h3 className="mb-3 text-lg font-semibold text-neutral-900">Portfolio Summary</h3>
              {data?.portfolio_error ? (
                <ErrorHint message={data.portfolio_error} />
              ) : (
                <StaggerContainer className="grid grid-cols-2 gap-3">
                  <StaggerItem>
                    <StatCard
                      label="Total Equity"
                      value={`$${formatNumber(portfolio?.total_equity)}`}
                      rawValue={portfolio?.total_equity ?? undefined}
                      prefix="$"
                    />
                  </StaggerItem>
                  <StaggerItem>
                    <StatCard
                      label="Cash Balance"
                      value={`$${formatNumber(portfolio?.cash_balance)}`}
                      rawValue={portfolio?.cash_balance ?? undefined}
                      prefix="$"
                    />
                  </StaggerItem>
                  <StaggerItem>
                    <StatCard
                      label="Total Exposure"
                      value={`$${formatNumber(portfolio?.total_exposure)}`}
                      rawValue={portfolio?.total_exposure ?? undefined}
                      prefix="$"
                    />
                  </StaggerItem>
                  <StaggerItem>
                    <StatCard
                      label="Open Positions"
                      value={String(positions.length)}
                      rawValue={positions.length}
                      decimals={0}
                    />
                  </StaggerItem>
                </StaggerContainer>
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
          </FadeInView>
        </section>

        {/* Row 2: Positions, Signals, Orders */}
        <section className="grid gap-6 lg:grid-cols-3">
          {/* Positions */}
          <FadeInView delay={0.05}>
            <div className="card">
              <h3 className="mb-3 text-lg font-semibold text-neutral-900">Positions</h3>
              {positions.length === 0 ? (
                <p className="text-sm text-neutral-400">No open positions</p>
              ) : (
                <StaggerContainer className="space-y-2">
                  {positions.map((pos) => (
                    <StaggerItem key={pos.asset}>
                      <div className="card-interactive flex items-center justify-between rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-sm">
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
                    </StaggerItem>
                  ))}
                </StaggerContainer>
              )}
            </div>
          </FadeInView>

          {/* Recent Signals */}
          <FadeInView delay={0.1}>
            <div className="card">
              <h3 className="mb-3 text-lg font-semibold text-neutral-900">Recent Signals</h3>
              {data?.signals_error ? (
                <ErrorHint message={data.signals_error} />
              ) : signals.length === 0 ? (
                <p className="text-sm text-neutral-400">No signals available</p>
              ) : (
                <StaggerContainer className="space-y-2">
                  {signals.slice(0, 5).map((sig, idx) => {
                    const isBuy = sig.direction.toUpperCase() === "BUY" || sig.direction.toUpperCase() === "LONG";
                    const isSell = sig.direction.toUpperCase() === "SELL" || sig.direction.toUpperCase() === "SHORT";
                    return (
                      <StaggerItem key={`${sig.asset}-${sig.feature_timestamp ?? idx}`}>
                        <div
                          className={`card-interactive rounded-lg border border-neutral-100 bg-neutral-50 p-3 ${
                            isBuy
                              ? "border-l-2 border-l-green-500"
                              : isSell
                                ? "border-l-2 border-l-red-500"
                                : ""
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium uppercase tracking-wider text-neutral-500">
                              {sig.asset}
                            </span>
                            <span className={`badge ${
                              isBuy
                                ? "bg-green-50 text-green-700"
                                : isSell
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
                      </StaggerItem>
                    );
                  })}
                </StaggerContainer>
              )}
            </div>
          </FadeInView>

          {/* Latest Orders */}
          <FadeInView delay={0.15}>
            <div className="card">
              <h3 className="mb-3 text-lg font-semibold text-neutral-900">Latest Orders</h3>
              {data?.orders_error ? (
                <ErrorHint message={data.orders_error} />
              ) : orders.length === 0 ? (
                <p className="text-sm text-neutral-400">No recent orders</p>
              ) : (
                <StaggerContainer className="space-y-2">
                  {orders.slice(0, 5).map((order, idx) => (
                    <StaggerItem key={order.order_id ?? idx}>
                      <div className="card-interactive rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-sm">
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
                    </StaggerItem>
                  ))}
                </StaggerContainer>
              )}
            </div>
          </FadeInView>
        </section>

        {/* Row 3: Statistics */}
        <section className="grid gap-6 lg:grid-cols-2">
          <FadeInView delay={0.05}>
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
          </FadeInView>
          <FadeInView delay={0.1}>
            <LiveFeed />
          </FadeInView>
        </section>
      </main>
    </PageTransition>
  );
}

export default function DashboardPage() {
  return (
    <AuthGuard>
      <DashboardContent />
    </AuthGuard>
  );
}
