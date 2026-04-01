"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  Expandable,
  motion,
} from "../../components/motion";

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
  if (s === "FILLED") return "bg-green-50 text-green-700";
  if (s === "CANCELLED" || s === "EXPIRED") return "bg-neutral-100 text-neutral-400";
  if (s === "REJECTED") return "bg-red-50 text-red-700";
  if (s === "PENDING" || s === "NEW" || s === "OPEN") return "bg-yellow-50 text-yellow-700";
  return "bg-neutral-100 text-neutral-400";
}

function sideBadgeClass(side: string): string {
  return side === "BUY"
    ? "bg-green-50 text-green-700"
    : "bg-red-50 text-red-700";
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
  const filterRef = useRef<HTMLDivElement>(null);

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

  // Calculate the underline position for the active filter tab
  const activeIdx = STATUS_FILTERS.indexOf(filter);

  return (
    <PageTransition>
      <main className="grid gap-6">
        <section className="card">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-2xl font-semibold text-neutral-900">Orders</h2>
            <div className="flex flex-wrap items-center gap-2">
              <div ref={filterRef} className="relative flex flex-wrap items-center gap-2">
                {STATUS_FILTERS.map((f, idx) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`relative rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                      filter === f
                        ? "border-neutral-900 bg-neutral-900 text-white"
                        : "border-neutral-200 text-neutral-500 hover:border-neutral-400"
                    }`}
                  >
                    {f}
                    {filter === f && (
                      <motion.div
                        layoutId="order-filter-underline"
                        className="absolute -bottom-1 left-1/4 right-1/4 h-0.5 rounded-full bg-neutral-900"
                        transition={{ type: "spring", stiffness: 400, damping: 30 }}
                      />
                    )}
                  </button>
                ))}
              </div>
              <button
                onClick={fetchOrders}
                className="btn-secondary ml-2 text-xs"
              >
                Refresh
              </button>
            </div>
          </div>
        </section>

        {loading ? (
          <StaggerContainer className="space-y-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <StaggerItem key={i}>
                <div className="card animate-pulse">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="h-4 w-20 rounded bg-neutral-200" />
                      <div className="h-5 w-12 rounded-full bg-neutral-200" />
                      <div className="h-5 w-16 rounded-full bg-neutral-200" />
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="h-4 w-16 rounded bg-neutral-100" />
                      <div className="h-4 w-24 rounded bg-neutral-100" />
                    </div>
                  </div>
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
        ) : error ? (
          <div className="card">
            <p className="text-red-500">{error}</p>
            <p className="mt-2 text-sm text-neutral-500">
              Make sure you are logged in and the order service is running.
            </p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="card">
            <p className="text-neutral-400">
              {filter === "ALL" ? "No orders found." : `No ${filter} orders found.`}
            </p>
          </div>
        ) : (
          <StaggerContainer className="space-y-3">
            {filtered.map((order) => {
              const isExpanded = expandedId === order.order_id;
              const isTerminal = TERMINAL_STATUSES.has(order.status.toUpperCase());
              const isCancelling = cancelling.has(order.order_id);

              return (
                <StaggerItem key={order.order_id}>
                  <article className="card">
                    <div
                      className="flex cursor-pointer flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                      onClick={() => setExpandedId(isExpanded ? null : order.order_id)}
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-semibold uppercase tracking-wider text-neutral-900">
                          {order.asset}
                        </span>
                        <span className={`badge ${sideBadgeClass(order.side)}`}>
                          {order.side}
                        </span>
                        <span className={`badge ${statusBadgeClass(order.status)}`}>
                          {order.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-sm text-neutral-500">
                        <span>Qty: {formatNumber(order.quantity, 4)}</span>
                        {order.price != null && <span>Price: ${formatNumber(order.price)}</span>}
                        <span className="text-xs">{formatTs(order.created_at)}</span>
                        {!isTerminal && (
                          <motion.button
                            whileHover={{ x: [0, -2, 2, -2, 2, 0] }}
                            transition={{ duration: 0.4 }}
                            onClick={(e) => {
                              e.stopPropagation();
                              cancelOrder(order.order_id);
                            }}
                            disabled={isCancelling}
                            className="rounded-lg border border-red-200 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-40"
                          >
                            {isCancelling ? "Cancelling..." : "Cancel"}
                          </motion.button>
                        )}
                      </div>
                    </div>

                    <Expandable open={isExpanded}>
                      <div className="mt-4 space-y-3 border-t border-neutral-200 pt-4">
                        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Order ID</p>
                            <p className="mt-1 font-mono text-xs text-neutral-600">{order.order_id}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Type</p>
                            <p className="mt-1 text-neutral-600">{order.order_type ?? "MARKET"}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Exchange</p>
                            <p className="mt-1 text-neutral-600">{order.exchange ?? "--"}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Updated</p>
                            <p className="mt-1 text-neutral-600">{formatTs(order.updated_at)}</p>
                          </div>
                        </div>

                        {order.filled_quantity != null && (
                          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm">
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Fill Info</p>
                            <p className="mt-1 text-neutral-600">
                              Filled: {formatNumber(order.filled_quantity, 4)} @ ${formatNumber(order.filled_price)}
                            </p>
                          </div>
                        )}

                        {order.reject_reason && (
                          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
                            <p className="text-xs font-medium uppercase tracking-wider text-red-600">Reject Reason</p>
                            <p className="mt-1 text-red-700">{order.reject_reason}</p>
                          </div>
                        )}

                        {order.protections && order.protections.length > 0 && (
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Protections</p>
                            <div className="mt-2 space-y-1">
                              {order.protections.map((p, idx) => (
                                <div key={idx} className="rounded-lg border border-neutral-100 bg-neutral-50 p-2 text-xs text-neutral-600">
                                  {p.type} {p.trigger_price != null ? `| Trigger: $${formatNumber(p.trigger_price)}` : ""}
                                  {p.limit_price != null ? ` | Limit: $${formatNumber(p.limit_price)}` : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {order.lifecycle_events && order.lifecycle_events.length > 0 && (
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">Lifecycle Events</p>
                            <div className="mt-2 space-y-1">
                              {order.lifecycle_events.map((ev, idx) => (
                                <div key={idx} className="flex items-center gap-3 rounded-lg border border-neutral-100 bg-neutral-50 p-2 text-xs">
                                  <span className="font-medium text-neutral-700">{ev.event}</span>
                                  <span className="text-neutral-400">{formatTs(ev.timestamp)}</span>
                                  {ev.details && (
                                    <span className="text-neutral-500">{JSON.stringify(ev.details)}</span>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </Expandable>
                  </article>
                </StaggerItem>
              );
            })}
          </StaggerContainer>
        )}
      </main>
    </PageTransition>
  );
}

export default function OrdersPage() {
  return (
    <AuthGuard>
      <OrdersContent />
    </AuthGuard>
  );
}
