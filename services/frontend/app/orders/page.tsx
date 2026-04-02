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
  if (s === "FILLED") return "bg-neutral-900 text-white";
  if (s === "CANCELLED" || s === "EXPIRED") return "bg-neutral-100 text-neutral-400";
  if (s === "REJECTED") return "bg-neutral-100 text-red-600";
  if (s === "PENDING" || s === "NEW" || s === "OPEN") return "bg-neutral-100 text-neutral-600";
  return "bg-neutral-100 text-neutral-400";
}

function sideBadgeClass(side: string): string {
  return side === "BUY"
    ? "bg-neutral-900 text-white"
    : "border border-neutral-900 text-neutral-900";
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
        setError(e instanceof Error ? e.message : "주문 로드 실패");
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
      const msg = e instanceof Error ? e.message : "취소 실패";
      alert(msg);
    } finally {
      setCancelling((prev) => {
        const next = new Set(prev);
        next.delete(orderId);
        return next;
      });
    }
  }

  const activeIdx = STATUS_FILTERS.indexOf(filter);

  return (
    <PageTransition>
      <main className="grid gap-6">
        <section className="rounded border border-neutral-200 bg-white p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">ORDERS</p>
              <h2 className="mt-1 text-2xl font-semibold text-neutral-900">주문</h2>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div ref={filterRef} className="relative flex flex-wrap items-center gap-1">
                {STATUS_FILTERS.map((f, idx) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`relative px-3 py-1.5 text-xs font-medium transition ${
                      filter === f
                        ? "text-neutral-900"
                        : "text-neutral-400 hover:text-neutral-600"
                    }`}
                  >
                    {f === "ALL" ? "전체" : f === "OPEN" ? "미체결" : f === "FILLED" ? "체결" : f === "CANCELLED" ? "취소됨" : f === "PENDING" ? "대기" : f === "REJECTED" ? "거부" : f}
                    {filter === f && (
                      <motion.div
                        layoutId="order-filter-underline"
                        className="absolute -bottom-0.5 left-2 right-2 h-px bg-neutral-900"
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
                새로고침
              </button>
            </div>
          </div>
        </section>

        {loading ? (
          <StaggerContainer className="space-y-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <StaggerItem key={i}>
                <div className="rounded border border-neutral-200 bg-white p-6 animate-pulse">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="h-4 w-20 rounded bg-neutral-200" />
                      <div className="h-5 w-12 rounded bg-neutral-100" />
                      <div className="h-5 w-16 rounded bg-neutral-100" />
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
          <div className="rounded border border-neutral-200 bg-white p-6">
            <p className="text-red-600 text-sm">{error}</p>
            <p className="mt-2 text-sm text-neutral-500">
              로그인 상태와 주문 서비스 연결을 확인해주세요.
            </p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded border border-neutral-200 bg-white p-6">
            <p className="text-neutral-400">
              {filter === "ALL" ? "주문 없음" : `${filter === "OPEN" ? "미체결" : filter === "FILLED" ? "체결" : filter === "CANCELLED" ? "취소됨" : filter === "PENDING" ? "대기" : filter === "REJECTED" ? "거부" : filter} 주문 없음`}
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
                  <article className="rounded border border-neutral-200 bg-white p-6 transition hover:border-neutral-300">
                    <div
                      className="flex cursor-pointer flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                      onClick={() => setExpandedId(isExpanded ? null : order.order_id)}
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-semibold uppercase tracking-wider text-neutral-900">
                          {order.asset}
                        </span>
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${sideBadgeClass(order.side)}`}>
                          {order.side}
                        </span>
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(order.status)}`}>
                          {order.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-sm text-neutral-500">
                        <span className="font-mono">수량: {formatNumber(order.quantity, 4)}</span>
                        {order.price != null && <span className="font-mono">가격: ${formatNumber(order.price)}</span>}
                        <span className="text-xs">{formatTs(order.created_at)}</span>
                        {!isTerminal && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              cancelOrder(order.order_id);
                            }}
                            disabled={isCancelling}
                            className="rounded border border-neutral-200 px-3 py-1 text-xs font-medium text-neutral-600 hover:border-neutral-400 disabled:opacity-40"
                          >
                            {isCancelling ? "취소 중..." : "취소"}
                          </button>
                        )}
                      </div>
                    </div>

                    <Expandable open={isExpanded}>
                      <div className="mt-4 space-y-3 border-t border-neutral-200 pt-4">
                        <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">주문 ID</p>
                            <p className="mt-1 font-mono text-xs text-neutral-600">{order.order_id}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">유형</p>
                            <p className="mt-1 text-neutral-600">{order.order_type ?? "MARKET"}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">거래소</p>
                            <p className="mt-1 text-neutral-600">{order.exchange ?? "--"}</p>
                          </div>
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">수정일</p>
                            <p className="mt-1 text-neutral-600">{formatTs(order.updated_at)}</p>
                          </div>
                        </div>

                        {order.filled_quantity != null && (
                          <div className="rounded border border-neutral-200 bg-white p-3 text-sm">
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">체결 정보</p>
                            <p className="mt-1 font-mono text-neutral-600">
                              체결: {formatNumber(order.filled_quantity, 4)} @ ${formatNumber(order.filled_price)}
                            </p>
                          </div>
                        )}

                        {order.reject_reason && (
                          <div className="rounded border border-neutral-200 bg-white p-3 text-sm">
                            <p className="text-xs font-medium uppercase tracking-wider text-red-600">거부 사유</p>
                            <p className="mt-1 text-neutral-600">{order.reject_reason}</p>
                          </div>
                        )}

                        {order.protections && order.protections.length > 0 && (
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">보호 설정</p>
                            <div className="mt-2 space-y-1">
                              {order.protections.map((p, idx) => (
                                <div key={idx} className="rounded border border-neutral-200 bg-white p-2 font-mono text-xs text-neutral-600">
                                  {p.type} {p.trigger_price != null ? `| 트리거: $${formatNumber(p.trigger_price)}` : ""}
                                  {p.limit_price != null ? ` | 지정가: $${formatNumber(p.limit_price)}` : ""}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {order.lifecycle_events && order.lifecycle_events.length > 0 && (
                          <div>
                            <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">주문 이력</p>
                            <div className="mt-2 space-y-1">
                              {order.lifecycle_events.map((ev, idx) => (
                                <div key={idx} className="flex items-center gap-3 rounded border border-neutral-200 bg-white p-2 text-xs">
                                  <span className="font-medium text-neutral-700">{ev.event}</span>
                                  <span className="text-neutral-400">{formatTs(ev.timestamp)}</span>
                                  {ev.details && (
                                    <span className="font-mono text-neutral-500">{JSON.stringify(ev.details)}</span>
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
