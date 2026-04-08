"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import { useToast } from "../../components/toast";
import { ConfirmDialog } from "../../components/confirm-dialog";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  Expandable,
  AnimatedNumber,
  motion,
} from "../../components/motion";

/* ── friendly mappings ───────────────────────────────────────── */
const ASSET_NAMES: Record<string, string> = {
  BTCUSDT: "비트코인",
  ETHUSDT: "이더리움",
  SOLUSDT: "솔라나",
  XRPUSDT: "리플",
  DOGEUSDT: "도지코인",
  ADAUSDT: "에이다",
  BNBUSDT: "바이낸스코인",
};

const SIDE_LABEL: Record<string, string> = { BUY: "매수", SELL: "매도" };

const STATUS_LABEL: Record<string, string> = {
  FILLED: "체결",
  REJECTED: "거부",
  CANCELLED: "취소",
  EXPIRED: "만료",
  PENDING: "대기",
  NEW: "대기",
  OPEN: "진행중",
};

function friendlyAsset(raw: string): string {
  return ASSET_NAMES[raw.toUpperCase()] ?? raw;
}

function friendlySide(raw: string): string {
  return SIDE_LABEL[raw.toUpperCase()] ?? raw;
}

function friendlyStatus(raw: string): string {
  return STATUS_LABEL[raw.toUpperCase()] ?? raw;
}

/* ── status dot colours ──────────────────────────────────────── */
function statusDotColor(status: string): string {
  const s = status.toUpperCase();
  if (s === "FILLED") return "bg-emerald-500/150";
  if (s === "REJECTED") return "bg-red-500/150";
  if (s === "CANCELLED" || s === "EXPIRED") return "bg-neutral-300";
  if (s === "PENDING" || s === "NEW") return "bg-amber-400";
  if (s === "OPEN") return "bg-white";
  return "bg-neutral-300";
}

function statusTextColor(status: string): string {
  const s = status.toUpperCase();
  if (s === "FILLED") return "text-emerald-400";
  if (s === "REJECTED") return "text-red-400";
  if (s === "CANCELLED" || s === "EXPIRED") return "text-neutral-400";
  if (s === "PENDING" || s === "NEW") return "text-amber-400";
  if (s === "OPEN") return "text-white";
  return "text-neutral-400";
}

/* ── relative time ───────────────────────────────────────────── */
function relativeTime(ts: string | undefined): string {
  if (!ts) return "--";
  try {
    const diff = Date.now() - new Date(ts).getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 0) return "방금";
    if (sec < 60) return `${sec}초 전`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}분 전`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}시간 전`;
    const day = Math.floor(hr / 24);
    if (day < 30) return `${day}일 전`;
    return new Date(ts).toLocaleDateString("ko-KR");
  } catch {
    return ts;
  }
}

function formatPrice(value: number | undefined | null): string {
  if (value == null) return "--";
  return "$" + value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatQty(value: number | undefined | null): string {
  if (value == null) return "--";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
  });
}

/* ── types ───────────────────────────────────────────────────── */
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
  lifecycle_events?: { event: string; timestamp: string; details?: Record<string, unknown> }[];
  protections?: { type: string; trigger_price?: number; limit_price?: number }[];
  filled_quantity?: number;
  filled_price?: number;
  reject_reason?: string;
}

type StatusFilter = "ALL" | "FILLED" | "REJECTED" | "CANCELLED" | "PENDING" | "OPEN";

const STATUS_FILTERS: StatusFilter[] = [
  "ALL",
  "PENDING",
  "OPEN",
  "FILLED",
  "CANCELLED",
  "REJECTED",
];
const FILTER_LABELS: Record<string, string> = {
  ALL: "전체",
  PENDING: "대기",
  OPEN: "진행중",
  FILLED: "체결",
  CANCELLED: "취소",
  REJECTED: "거부",
};

const TERMINAL_STATUSES = new Set(["FILLED", "CANCELLED", "REJECTED", "EXPIRED"]);

/* ── Order timeline item ─────────────────────────────────────── */
function OrderItem({
  order,
  isExpanded,
  onToggle,
  onCancel,
  isCancelling,
}: {
  order: Order;
  isExpanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  isCancelling: boolean;
}) {
  const isTerminal = TERMINAL_STATUSES.has(order.status.toUpperCase());
  const isBuy = order.side === "BUY";

  return (
    <article className="relative rounded-xl border border-white/[0.06] bg-white/[0.02] transition-all duration-150 hover:bg-white/[0.04] hover:border-white/[0.10]">
      {/* main row — clickable */}
      <button
        onClick={onToggle}
        className="flex w-full items-start gap-4 p-5 text-left"
      >
        {/* side indicator */}
        <div
          className={`mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full text-sm font-bold ${
            isBuy
              ? "bg-emerald-500/15 text-emerald-400"
              : "bg-red-500/15 text-red-400"
          }`}
        >
          {friendlySide(order.side)}
        </div>

        {/* content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-zinc-200">
              {friendlyAsset(order.asset)}
            </span>

            {/* status dot + text */}
            <span className="flex items-center gap-1.5">
              <span
                className={`inline-block h-2 w-2 rounded-full ${statusDotColor(
                  order.status
                )}`}
              />
              <span
                className={`text-xs font-medium ${statusTextColor(
                  order.status
                )}`}
              >
                {friendlyStatus(order.status)}
              </span>
            </span>
          </div>

          {/* price + qty summary */}
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-zinc-400">
            <span className="font-mono tabular-nums">
              {formatQty(order.filled_quantity ?? order.quantity)}
              {order.filled_price != null
                ? ` @ ${formatPrice(order.filled_price)}`
                : order.price != null
                ? ` @ ${formatPrice(order.price)}`
                : ""}
            </span>
            {order.filled_quantity != null &&
              order.filled_price != null && (
                <span className="font-mono text-xs font-medium tabular-nums text-zinc-50">
                  총 {formatPrice(order.filled_quantity * order.filled_price)}
                </span>
              )}
          </div>
        </div>

        {/* right side: time + cancel */}
        <div className="flex flex-shrink-0 flex-col items-end gap-2">
          <span className="text-xs text-neutral-400">
            {relativeTime(order.created_at)}
          </span>
          {!isTerminal && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onCancel();
              }}
              disabled={isCancelling}
              className="rounded-full border border-white/[0.06] px-3 py-1 text-[11px] font-medium text-zinc-500 transition duration-150 hover:border-white/[0.10] hover:text-zinc-300 disabled:opacity-40"
            >
              {isCancelling ? "취소 중..." : "주문 취소"}
            </button>
          )}
        </div>
      </button>

      {/* expandable detail */}
      <Expandable open={isExpanded}>
        <div className="border-t border-white/[0.06] px-5 pb-5 pt-4">
          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">주문 유형</p>
              <p className="mt-0.5 text-sm text-zinc-200">
                {order.order_type === "LIMIT"
                  ? "지정가"
                  : order.order_type === "MARKET"
                  ? "시장가"
                  : order.order_type ?? "시장가"}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">거래소</p>
              <p className="mt-0.5 text-sm text-zinc-200">
                {order.exchange ?? "--"}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">주문 수량</p>
              <p className="mt-0.5 font-mono text-sm font-medium tabular-nums text-zinc-50">
                {formatQty(order.quantity)}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">주문 가격</p>
              <p className="mt-0.5 font-mono text-sm font-medium tabular-nums text-zinc-50">
                {formatPrice(order.price)}
              </p>
            </div>
          </div>

          {/* fill info */}
          {order.filled_quantity != null && (
            <div className="mt-4 rounded-lg bg-green-500/10 border border-green-500/15 p-3">
              <p className="text-[11px] font-medium uppercase tracking-wider text-green-500">
                체결 정보
              </p>
              <p className="mt-1 font-mono text-sm font-medium tabular-nums text-zinc-50">
                {formatQty(order.filled_quantity)} @ {formatPrice(order.filled_price)}
              </p>
            </div>
          )}

          {/* reject reason */}
          {order.reject_reason && (
            <div className="mt-4 rounded-lg bg-red-500/10 border border-red-500/15 p-3">
              <p className="text-[11px] font-medium uppercase tracking-wider text-red-500">
                거부 사유
              </p>
              <p className="mt-1 text-sm text-zinc-400">
                {order.reject_reason}
              </p>
            </div>
          )}

          {/* timestamps */}
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-zinc-500">
            <span>
              생성: {order.created_at ? new Date(order.created_at).toLocaleString("ko-KR") : "--"}
            </span>
            {order.updated_at && (
              <span>
                수정: {new Date(order.updated_at).toLocaleString("ko-KR")}
              </span>
            )}
          </div>
        </div>
      </Expandable>
    </article>
  );
}

/* ── Main content ────────────────────────────────────────────── */
function OrdersContent() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("ALL");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [cancelTarget, setCancelTarget] = useState<string | null>(null);
  const toast = useToast();

  const fetchOrders = useCallback(() => {
    setLoading(true);
    setError(null);
    gatewayFetch("/orders")
      .then((data) => {
        const items = Array.isArray(data)
          ? data
          : (data as { orders?: Order[] }).orders ?? [];
        setOrders(items as Order[]);
        setError(null);
        setLastUpdated(Date.now());
      })
      .catch((e) => {
        setOrders([]);
        setError(e instanceof Error ? e.message : "주문 로드 실패");
        toast.show("error", "주문 데이터를 불러오지 못했습니다");
      })
      .finally(() => setLoading(false));
  }, [toast]);

  useEffect(() => {
    fetchOrders();
  }, [fetchOrders]);

  const filtered =
    filter === "ALL"
      ? orders
      : orders.filter((o) => o.status.toUpperCase() === filter);

  async function cancelOrder(orderId: string) {
    setCancelling((prev) => new Set(prev).add(orderId));
    try {
      await gatewayFetch(`/orders/${orderId}`, { method: "DELETE" });
      toast.show("success", "주문이 취소되었습니다");
      fetchOrders();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "취소 실패";
      toast.show("error", msg);
    } finally {
      setCancelling((prev) => {
        const next = new Set(prev);
        next.delete(orderId);
        return next;
      });
    }
  }

  return (
    <PageTransition>
      <ConfirmDialog
        open={cancelTarget !== null}
        title="주문 취소"
        message="이 주문을 취소하시겠습니까? 이 작업은 되돌릴 수 없습니다."
        confirmText="주문 취소"
        danger
        onConfirm={() => {
          if (cancelTarget) cancelOrder(cancelTarget);
          setCancelTarget(null);
        }}
        onCancel={() => setCancelTarget(null)}
      />
      <main className="grid gap-6">
        {/* Header */}
        <section className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
                Orders
              </p>
              <h2 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-50">
                주문 내역
              </h2>
              {lastUpdated && (
                <span className="text-[10px] text-zinc-500">
                  마지막 업데이트: {new Date(lastUpdated).toLocaleTimeString("ko-KR")}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={fetchOrders}
                className="rounded-full border border-white/[0.06] px-4 py-1.5 text-xs font-medium text-zinc-400 transition duration-150 hover:border-white/[0.10]"
              >
                새로고침
              </button>
            </div>
          </div>

          {/* Filter tabs */}
          <div className="mt-4 flex flex-wrap items-center gap-1">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`relative rounded-full px-4 py-1.5 text-xs font-medium transition ${
                  filter === f
                    ? "bg-white text-black"
                    : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
                }`}
              >
                {FILTER_LABELS[f] ?? f}
                {filter === f && (
                  <motion.div
                    layoutId="order-filter-pill"
                    className="absolute inset-0 rounded-full bg-white -z-10"
                    transition={{ type: "spring", stiffness: 400, damping: 30 }}
                  />
                )}
              </button>
            ))}
          </div>
        </section>

        {/* Summary counts */}
        {!loading && !error && orders.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              {
                label: "전체",
                count: orders.length,
                color: "text-white",
              },
              {
                label: "체결",
                count: orders.filter(
                  (o) => o.status.toUpperCase() === "FILLED"
                ).length,
                color: "text-emerald-400",
              },
              {
                label: "대기/진행",
                count: orders.filter((o) =>
                  ["PENDING", "NEW", "OPEN"].includes(o.status.toUpperCase())
                ).length,
                color: "text-amber-400",
              },
              {
                label: "취소/거부",
                count: orders.filter((o) =>
                  ["CANCELLED", "REJECTED", "EXPIRED"].includes(
                    o.status.toUpperCase()
                  )
                ).length,
                color: "text-neutral-400",
              },
            ].map((s) => (
              <div
                key={s.label}
                className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-4 text-center"
              >
                <p className="text-xs font-medium text-zinc-500">{s.label}</p>
                <p className={`mt-1 font-mono text-2xl font-bold tracking-tighter tabular-nums ${s.color === "text-white" ? "text-zinc-50" : s.color}`}>
                  <AnimatedNumber value={s.count} decimals={0} />
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Content */}
        {loading ? (
          <StaggerContainer className="space-y-3">
            {[0, 1, 2, 3].map((i) => (
              <StaggerItem key={i}>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-5 animate-pulse">
                  <div className="flex items-center gap-4">
                    <div className="h-10 w-10 rounded-full bg-white/[0.06]" />
                    <div className="flex-1 space-y-2">
                      <div className="h-4 w-32 rounded bg-white/[0.06]" />
                      <div className="h-3 w-48 rounded bg-white/[0.02]" />
                    </div>
                    <div className="h-3 w-16 rounded bg-white/[0.02]" />
                  </div>
                </div>
              </StaggerItem>
            ))}
          </StaggerContainer>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-sm text-zinc-500">데이터를 불러오는 중 오류가 발생했습니다</p>
            <p className="text-xs text-zinc-500">{error}</p>
            <button onClick={() => { setError(null); fetchOrders(); }} className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black">
              다시 시도
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-12 text-center">
            <p className="text-sm text-zinc-400">
              {filter === "ALL"
                ? "아직 주문이 없습니다"
                : `${FILTER_LABELS[filter] ?? filter} 주문이 없습니다`}
            </p>
          </div>
        ) : (
          <StaggerContainer className="space-y-3">
            {filtered.map((order) => (
              <StaggerItem key={order.order_id}>
                <OrderItem
                  order={order}
                  isExpanded={expandedId === order.order_id}
                  onToggle={() =>
                    setExpandedId(
                      expandedId === order.order_id ? null : order.order_id
                    )
                  }
                  onCancel={() => setCancelTarget(order.order_id)}
                  isCancelling={cancelling.has(order.order_id)}
                />
              </StaggerItem>
            ))}
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
