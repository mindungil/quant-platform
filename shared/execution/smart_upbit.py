"""Upbit 스마트 실행: TWAP slicing + limit-chase.

주문을 "그대로 시장가로 한방에" 대신:
  - 큰 주문(THRESHOLD 초과) → TWAP으로 N개 slice
  - 각 slice는 먼저 best-price limit 주문 → X초 내 미체결 시 시장가 fallback

왜 필요한가?
  - Upbit 수수료 = 0.05% (maker/taker 동일) — BUT 시장가는 spread cost 추가
  - 주요 코인 호가 spread 보통 2-5 bps → 연간 턴오버 4.0 기준 **연 8-20 bps 절약 가능**
  - 큰 주문은 호가창 깊이 이상 갈 경우 slippage 수십 bps

제약:
  - Upbit min order 5,000 KRW → slice size는 5k/count 이상 유지
  - Slice 간 인위적 대기로 rate limit 여유 확보
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from shared.execution.impact_model import estimate_impact, max_safe_slice
from shared.execution.risk_limits import OrderResult
from shared.execution.upbit import MIN_ORDER_KRW, UpbitConnector, _to_market
from shared.execution.upbit_l2 import (
    OrderbookSnapshot,
    UpbitL2Fetcher,
    _parse_upbit_orderbook,
    estimate_queue_position,
)

logger = logging.getLogger(__name__)


@dataclass
class SmartExecConfig:
    """실행 파라미터."""
    twap_threshold_krw: float = 500_000      # 50만원 이상 → TWAP (legacy fallback)
    twap_slices: int = 4                     # 기본 4개 (legacy fallback, dynamic override)
    twap_interval_sec: float = 15            # slice 간 15초
    limit_timeout_sec: float = 10            # limit 주문 기다리는 시간
    limit_offset_bps: float = 2              # best bid/ask에서 2bps 유리하게 (진입)
    max_slippage_bps: float = 30             # slice당 slippage > 30bps → halt
    # L2-aware dynamic sizing. When True, override twap_threshold/twap_slices
    # using the impact model — slice so expected impact ≤ max_impact_bps each.
    use_l2_sizing: bool = True
    max_impact_bps: float = 8.0              # per-slice impact budget
    impact_k_sqrt: float = 0.5               # tail-extrapolation coefficient
    # Adaptive limit-order repricing. Cancel + requote if queue position
    # grows beyond `requote_queue_multiplier` × our slice size while waiting.
    enable_adaptive_requote: bool = True
    requote_queue_multiplier: float = 3.0
    max_requotes: int = 2


@dataclass
class SliceResult:
    slice_num: int
    side: str
    quantity: float
    filled_quantity: float
    avg_price: float
    status: str
    method: str  # "limit" or "market"
    slippage_bps: float = 0.0
    error: str = ""


@dataclass
class SmartExecResult:
    symbol: str
    side: str
    target_quantity: float
    total_filled: float
    avg_fill_price: float
    slices: list[SliceResult] = field(default_factory=list)
    total_elapsed_sec: float = 0.0

    @property
    def fill_rate(self) -> float:
        return self.total_filled / self.target_quantity if self.target_quantity > 0 else 0.0


def _orderbook_best(connector: UpbitConnector, market: str) -> tuple[float, float] | None:
    """best_bid, best_ask 조회."""
    try:
        data = connector._request("GET", "/v1/orderbook", params={"markets": market})
        units = data[0]["orderbook_units"]
        return float(units[0]["bid_price"]), float(units[0]["ask_price"])
    except Exception:
        return None


def _fetch_snapshot(connector: UpbitConnector, market: str) -> OrderbookSnapshot | None:
    try:
        raw = connector._request("GET", "/v1/orderbook", params={"markets": market})
        if not raw:
            return None
        return _parse_upbit_orderbook(raw[0] if isinstance(raw, list) else raw)
    except Exception:
        return None


def _plan_slices(
    snapshot: OrderbookSnapshot | None,
    side: str,
    total_notional: float,
    cfg: SmartExecConfig,
) -> int:
    """Decide slice count. L2 path uses max_safe_slice; else fall back."""
    if cfg.use_l2_sizing and snapshot is not None:
        safe = max_safe_slice(snapshot, side, cfg.max_impact_bps, k_sqrt=cfg.impact_k_sqrt)
        if safe > 0:
            n = max(1, int(total_notional // max(safe, MIN_ORDER_KRW)))
            # Enforce min-order viability + a hard ceiling so we don't
            # split a 10 KRW order into 100 slices.
            n = min(n, max(1, int(total_notional // MIN_ORDER_KRW)))
            return max(1, min(n, 20))
    # Legacy fallback
    if total_notional < cfg.twap_threshold_krw:
        return 1
    return cfg.twap_slices


def _execute_slice(
    connector: UpbitConnector,
    symbol: str,
    side: str,
    quantity: float,
    cfg: SmartExecConfig,
    slice_num: int,
) -> SliceResult:
    """단일 slice 실행: limit 먼저 → 타임아웃 시 남은 만큼 시장가."""
    market = _to_market(symbol)
    best = _orderbook_best(connector, market)
    if best is None:
        # 호가창 실패 → 그냥 시장가
        r = connector.place_market_order(symbol, side, quantity)
        return SliceResult(
            slice_num=slice_num, side=side, quantity=quantity,
            filled_quantity=r.filled_quantity, avg_price=r.avg_price,
            status=r.status, method="market_fallback_no_orderbook",
            error=r.error,
        )

    best_bid, best_ask = best
    ref_price = best_ask if side.upper() == "BUY" else best_bid

    # Limit price: 진입에 유리하게 offset (BUY=bid 근처, SELL=ask 근처)
    offset = ref_price * cfg.limit_offset_bps * 1e-4
    if side.upper() == "BUY":
        limit_price = best_bid + offset  # bid 위로 살짝
    else:
        limit_price = best_ask - offset  # ask 아래로 살짝

    # Upbit 호가 단위 (tick size) 반올림
    limit_price = _round_to_tick(limit_price)

    notional = quantity * limit_price
    if notional < MIN_ORDER_KRW:
        return SliceResult(
            slice_num=slice_num, side=side, quantity=quantity,
            filled_quantity=0.0, avg_price=0.0,
            status="REJECTED", method="skipped",
            error=f"below_min_{notional:.0f}_KRW",
        )

    # Limit 주문 시도
    r_limit = connector.place_limit_order(symbol, side, quantity, limit_price)
    if r_limit.status in ("REJECTED", "ERROR") or not r_limit.order_id:
        # 즉시 실패 → 시장가
        r = connector.place_market_order(symbol, side, quantity)
        return SliceResult(
            slice_num=slice_num, side=side, quantity=quantity,
            filled_quantity=r.filled_quantity, avg_price=r.avg_price,
            status=r.status, method="market_after_limit_reject",
            error=r.error,
        )

    # Limit이 체결될 때까지 대기 (+ adaptive requote)
    deadline = time.monotonic() + cfg.limit_timeout_sec
    filled = 0.0
    avg_price = 0.0
    requotes = 0
    slice_notional = quantity * limit_price
    while time.monotonic() < deadline:
        time.sleep(1.0)
        status = connector.get_order_status(r_limit.order_id)
        if "error" in status:
            break
        state = status.get("state", "").lower()
        executed = float(status.get("executed_volume", 0))
        if executed > 0:
            filled = executed
            # avg_price: trades 배열에서 계산
            trades = status.get("trades", [])
            if trades:
                total_notional = sum(float(t["price"]) * float(t["volume"]) for t in trades)
                total_vol = sum(float(t["volume"]) for t in trades)
                avg_price = total_notional / total_vol if total_vol > 0 else limit_price
        if state in ("done", "filled") and filled >= quantity * 0.999:
            slippage_bps = (avg_price - ref_price) / ref_price * 1e4
            if side.upper() == "SELL":
                slippage_bps = -slippage_bps
            return SliceResult(
                slice_num=slice_num, side=side, quantity=quantity,
                filled_quantity=filled, avg_price=avg_price,
                status="FILLED", method="limit",
                slippage_bps=float(slippage_bps),
            )

        # Adaptive requote: if queue ahead of us grew > `multiplier * our size`,
        # market is moving away → cancel and reprice.
        if (
            cfg.enable_adaptive_requote
            and requotes < cfg.max_requotes
            and filled < quantity * 0.3
        ):
            snap = _fetch_snapshot(connector, market)
            if snap is not None:
                ahead = estimate_queue_position(snap, side, limit_price)
                if ahead > slice_notional * cfg.requote_queue_multiplier:
                    connector.cancel_order(symbol, r_limit.order_id)
                    best_bid, best_ask = snap.best_bid or 0, snap.best_ask or 0
                    new_ref = best_ask if side.upper() == "BUY" else best_bid
                    if new_ref <= 0:
                        break
                    # Cross one tick toward the other side to chase fill
                    if side.upper() == "BUY":
                        limit_price = _round_to_tick(new_ref + new_ref * 1e-4)
                    else:
                        limit_price = _round_to_tick(new_ref - new_ref * 1e-4)
                    remaining = quantity - filled
                    r_limit = connector.place_limit_order(symbol, side, remaining, limit_price)
                    if not r_limit.order_id:
                        break
                    slice_notional = remaining * limit_price
                    requotes += 1
                    continue

    # 타임아웃 → 남은 수량 시장가 + limit 취소
    connector.cancel_order(symbol, r_limit.order_id)
    remaining = quantity - filled
    if remaining <= 0:
        return SliceResult(
            slice_num=slice_num, side=side, quantity=quantity,
            filled_quantity=filled, avg_price=avg_price,
            status="FILLED", method="limit_late",
        )

    r_mkt = connector.place_market_order(symbol, side, remaining)
    combined_filled = filled + r_mkt.filled_quantity
    if combined_filled > 0 and avg_price > 0:
        combined_avg = (avg_price * filled + r_mkt.avg_price * r_mkt.filled_quantity) / combined_filled
    else:
        combined_avg = r_mkt.avg_price or avg_price

    slip = (combined_avg - ref_price) / ref_price * 1e4 if ref_price > 0 else 0.0
    if side.upper() == "SELL":
        slip = -slip

    return SliceResult(
        slice_num=slice_num, side=side, quantity=quantity,
        filled_quantity=combined_filled, avg_price=combined_avg,
        status=r_mkt.status, method="limit_then_market",
        slippage_bps=float(slip),
        error=r_mkt.error,
    )


def _round_to_tick(price: float) -> float:
    """Upbit KRW 마켓 호가 단위 반올림.

    https://docs.upbit.com/docs/market-info-trade-price-detail
    """
    if price >= 2_000_000: tick = 1000
    elif price >= 1_000_000: tick = 500
    elif price >= 500_000: tick = 100
    elif price >= 100_000: tick = 50
    elif price >= 10_000: tick = 10
    elif price >= 1_000: tick = 1
    elif price >= 100: tick = 0.1
    elif price >= 10: tick = 0.01
    elif price >= 1: tick = 0.001
    else: tick = 0.0001
    return round(round(price / tick) * tick, 8)


def execute_smart(
    connector: UpbitConnector,
    symbol: str,
    side: str,
    quantity: float,
    price_hint: float,
    cfg: SmartExecConfig | None = None,
) -> SmartExecResult:
    """TWAP + limit-chase.

    price_hint: 사이즈 판단용 현재가 (KRW). TWAP 분할 여부 계산에 사용.
    """
    cfg = cfg or SmartExecConfig()
    start = time.monotonic()

    notional = quantity * price_hint
    market = _to_market(symbol)

    # L2-aware sizing: pull a single snapshot up front and plan slices
    snapshot = _fetch_snapshot(connector, market) if cfg.use_l2_sizing else None
    n = _plan_slices(snapshot, side, notional, cfg)

    if n <= 1:
        # 작은 주문 → 단일 slice (여전히 limit-chase 시도)
        slices = [_execute_slice(connector, symbol, side, quantity, cfg, slice_num=1)]
    else:
        # TWAP: 균등 분할 (마지막 slice는 누적 잔량 보정 위해 round 보정 가능하지만
        # 여기서는 단순 균등 — 부분 체결 잔량은 호출자가 재제출)
        slice_qty = quantity / n
        slices: list[SliceResult] = []
        total_slip = 0.0
        for i in range(n):
            s = _execute_slice(connector, symbol, side, slice_qty, cfg, slice_num=i + 1)
            slices.append(s)
            total_slip += abs(s.slippage_bps)
            # 누적 slippage 과도 시 중단
            if total_slip / (i + 1) > cfg.max_slippage_bps:
                logger.warning(
                    "smart_exec_slippage_halt",
                    extra={"avg_slippage_bps": total_slip / (i + 1), "slice": i + 1},
                )
                break
            if i < n - 1:
                time.sleep(cfg.twap_interval_sec)

    total_filled = sum(s.filled_quantity for s in slices)
    total_notional = sum(s.filled_quantity * s.avg_price for s in slices if s.avg_price > 0)
    avg_fill = total_notional / total_filled if total_filled > 0 else 0.0

    return SmartExecResult(
        symbol=symbol,
        side=side.upper(),
        target_quantity=quantity,
        total_filled=total_filled,
        avg_fill_price=avg_fill,
        slices=slices,
        total_elapsed_sec=time.monotonic() - start,
    )
