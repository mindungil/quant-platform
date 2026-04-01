"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";

interface OrderEvent {
  event: string;
  timestamp: string;
  details?: Record<string, unknown>;
}

interface OrderProtection {
  type: string;
  trigger_price?: number;
  limit_price?: number;
}

interface Order {
  order_id: string;
  asset: string;
  side: "BUY" | "SELL";
  quantity: number;
  price?: number;
  order_type?: string;
  status: string;
  created_at: string;
  updated_at?: string;
  exchange?: string;
  lifecycle_events?: OrderEvent[];
  protections?: OrderProtection[];
  filled_quantity?: number;
  filled_price?: number;
  reject_reason?: string;
}

type StatusFilter = "ALL" | "FILLED" | "REJECTED" | "CANCELLED" | "PENDING" | "OPEN";

const STATUS_FILTERS: StatusFilter[] = ["ALL", "PENDING", "OPEN", "FILLED", "CANCELLED", "REJECTED"];

const TERMINAL_STATUSES = new Set(["FILLED", "CANCELLED", "REJECTED", "EXPIRED"]);

function statusBadgeClass(status: string): string {
  const s = status.toUpperCase();
  if (s === "FILLED") return "bg-green-500/20 text-green-400";
  if (s === "CANCELLED" || s === "EXPIRED") return "bg-white/10 text-white/60";
  if (s === "REJECTED") return "bg-red-500/20 text-red-400";
  if (s === "PENDING" || s === "NEW" || s === "OPEN") return "bg-sand/20 text-sand";
  return "bg-white/10 text-white/60";
}

function sideBadgeClass(side: string): string {
  return side === "BUY"
    ? "bg-green-500/20 text-green-400"
    : "bg-red-500/20 text-red-400";
}

function formatTs(ts: string | undefined): string {
  if (!ts) return "--";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function formatNumber(value: number | undefined | null, decimals = 2): string {
  if (value == null) return "--";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function OrdersContent() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("ALL");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());

  const fetchOrders = useCallback(() => {
    setLoading(true);
    gatewayFetch("/orders")
      .then((data) => {
        const items = Array.isArray(data) ? data : (data as { orders?: Order[] }).orders ?? [];
        setOrders(items as Order[]);
        setError(null);
      })
      .catch((e) => {
        setOrders([]);
        setError(e instanceof Error ? e.message : "Failed to load orders");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchOrders();
  }, [fetchOrders]);

  const filtered = filter === "ALL"
    ? orders
    : orders.filter((o) => o.status.toUpperCase() === filter);

  async function cancelOrder(orderId: string) {
    setCancelling((prev) => new Set(prev).add(orderId));
    try {
      await gatewayFetch(`/orders/${orderId}`, { method: "DELETE" });
      fetchOrders();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Cancel failed";
      alert(msg);
    } finally {
      setCancelling((prev) => {
        const next = new Set(prev);
        next.delete(orderId);
        return next;
      });
    }
  }

  return (
    <main className="grid gap-6">
      <section className="panel">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-2xl font-semibold">Orders</h2>
          <div className="flex flex-wrap items-center gap-2">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded-full border px-3 py-1 text-xs transition ${
                  filter === f
                    ? "border-sand bg-sand/20 text-sand"
                    : "border-white/10 text-white/60 hover:bg-white/10"
                }`}
              >
                {f}
              </button>
            ))}
            <button
              onClick={fetchOrders}
              className="ml-2 rounded-full border border-white/10 px-3 py-1 text-xs text-white/60 hover:bg-white/10"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      {loading ? (
        <div className="panel animate-pulse">
          <p className="text-white/60">Loading orders...</p>
        </div>
      ) : error ? (
        <div className="panel">
          <p className="text-red-400">{error}</p>
          <p className="mt-2 text-sm text-white/60">
            Make sure you are logged in and the order service is running.
          </p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel">
          <p className="text-white/50">
            {filter === "ALL" ? "No orders found." : `No ${filter} orders found.`}
          </p>
        </div>
      ) : (
        <section className="space-y-3">
          {filtered.map((order) => {
            const isExpanded = expandedId === order.order_id;
            const isTerminal = TERMINAL_STATUSES.has(order.status.toUpperCase());
            const isCancelling = cancelling.has(order.order_id);

            return (
              <article key={order.order_id} className="panel">
                <div
                  className="flex cursor-pointer flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                  onClick={() => setExpandedId(isExpanded ? null : order.order_id)}
                >
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-semibold uppercase tracking-wider text-mint">
                      {order.asset}
                    </span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${sideBadgeClass(order.side)}`}>
                      {order.side}
                    </span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(order.status)}`}>
                      {order.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-sm text-white/60">
                    <span>Qty: {formatNumber(order.quantity, 4)}</span>
                    {order.price != null && <span>Price: ${formatNumber(order.price)}</span>}
                    <span className="text-xs">{formatTs(order.created_at)}</span>
                    {!isTerminal && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          cancelOrder(order.order_id);
                        }}
                        disabled={isCancelling}
                        className="rounded-full border border-red-400/30 px-3 py-1 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-40"
                      >
                        {isCancelling ? "Cancelling..." : "Cancel"}
                      </button>
                    )}
                  </div>
                </div>

                {isExpanded && (
                  <div className="mt-4 space-y-3 border-t border-white/10 pt-4">
                    <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                      <div>
                        <p className="text-xs uppercase tracking-wider text-mint">Order ID</p>
                        <p className="mt-1 font-mono text-xs text-white/70">{order.order_id}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-wider text-mint">Type</p>
                        <p className="mt-1 text-white/70">{order.order_type ?? "MARKET"}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-wider text-mint">Exchange</p>
                        <p className="mt-1 text-white/70">{order.exchange ?? "--"}</p>
                      </div>
                      <div>
                        <p className="text-xs uppercase tracking-wider text-mint">Updated</p>
                        <p className="mt-1 text-white/70">{formatTs(order.updated_at)}</p>
                      </div>
                    </div>

                    {order.filled_quantity != null && (
                      <div className="rounded-xl bg-black/20 p-3 text-sm">
                        <p className="text-xs uppercase tracking-wider text-mint">Fill Info</p>
                        <p className="mt-1 text-white/70">
                          Filled: {formatNumber(order.filled_quantity, 4)} @ ${formatNumber(order.filled_price)}
                        </p>
                      </div>
                    )}

                    {order.reject_reason && (
                      <div className="rounded-xl bg-red-500/10 p-3 text-sm">
                        <p className="text-xs uppercase tracking-wider text-red-400">Reject Reason</p>
                        <p className="mt-1 text-red-300">{order.reject_reason}</p>
                      </div>
                    )}

                    {order.protections && order.protections.length > 0 && (
                      <div>
                        <p className="text-xs uppercase tracking-wider text-mint">Protections</p>
                        <div className="mt-2 space-y-1">
                          {order.protections.map((p, idx) => (
                            <div key={idx} className="rounded-xl bg-black/20 p-2 text-xs text-white/70">
                              {p.type} {p.trigger_price != null ? `| Trigger: $${formatNumber(p.trigger_price)}` : ""}
                              {p.limit_price != null ? ` | Limit: $${formatNumber(p.limit_price)}` : ""}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {order.lifecycle_events && order.lifecycle_events.length > 0 && (
                      <div>
                        <p className="text-xs uppercase tracking-wider text-mint">Lifecycle Events</p>
                        <div className="mt-2 space-y-1">
                          {order.lifecycle_events.map((ev, idx) => (
                            <div key={idx} className="flex items-center gap-3 rounded-xl bg-black/20 p-2 text-xs">
                              <span className="font-medium text-white/80">{ev.event}</span>
                              <span className="text-white/40">{formatTs(ev.timestamp)}</span>
                              {ev.details && (
                                <span className="text-white/50">{JSON.stringify(ev.details)}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}

export default function OrdersPage() {
  return (
    <AuthGuard>
      <OrdersContent />
    </AuthGuard>
  );
}
