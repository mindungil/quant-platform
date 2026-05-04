"""Risk limits for live trading.

Pre-trade and portfolio-level risk checks. All limits are enforced
before any order is sent to the exchange.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskLimits:
    """Portfolio-level risk constraints."""
    max_position_per_symbol: float = 0.20    # max fraction of equity per symbol
    max_total_exposure: float = 1.50         # max gross exposure (sum of |positions|)
    max_drawdown_halt: float = 0.15          # halt all trading if DD exceeds this
    max_single_order_notional: float = 0.05  # max single order as fraction of equity
    max_daily_turnover: float = 2.0          # max daily turnover (sum of |trades|/equity)
    min_order_size_usd: float = 10.0         # minimum order size in USD


@dataclass
class TradeOrder:
    """A proposed trade to execute."""
    symbol: str
    side: str          # "BUY" or "SELL"
    quantity: float    # in base asset units
    order_type: str = "MARKET"  # "MARKET" or "LIMIT"
    price: float | None = None  # required for LIMIT orders
    reduce_only: bool = False


@dataclass
class OrderResult:
    """Result of an order execution attempt."""
    symbol: str
    side: str
    quantity: float
    filled_quantity: float
    avg_price: float
    status: str        # "FILLED", "PARTIALLY_FILLED", "REJECTED", "ERROR"
    order_id: str = ""
    client_order_id: str = ""  # echoed back from exchange when caller passed one
    error: str = ""


@dataclass
class ExecutionResult:
    """Aggregate result of a reconciliation cycle."""
    orders_sent: int
    orders_filled: int
    orders_failed: int
    total_notional: float
    results: list[OrderResult]


def check_pre_trade(
    order: TradeOrder,
    limits: RiskLimits,
    equity: float,
    current_positions: dict[str, float],
    daily_turnover: float,
    current_drawdown: float,
) -> tuple[bool, str]:
    """Pre-trade risk check. Returns (passed, reason)."""
    # DD circuit breaker
    if current_drawdown >= limits.max_drawdown_halt:
        return False, f"Drawdown halt: {current_drawdown:.1%} >= {limits.max_drawdown_halt:.1%}"

    # Daily turnover limit
    if daily_turnover >= limits.max_daily_turnover:
        return False, f"Daily turnover limit: {daily_turnover:.2f} >= {limits.max_daily_turnover:.2f}"

    # Single order size — price must be provided (or estimated) for notional checks
    order_notional = order.quantity * (order.price or 0)
    if order.price is None or order.price <= 0:
        if not order.reduce_only:
            return False, "Market order missing price estimate — cannot validate notional size"

    if equity > 0 and order_notional / equity > limits.max_single_order_notional:
        return False, f"Order too large: {order_notional/equity:.1%} of equity"

    # Min order size
    if order_notional < limits.min_order_size_usd and not order.reduce_only:
        return False, f"Order too small: ${order_notional:.2f} < ${limits.min_order_size_usd}"

    # Position limit per symbol
    current_pos = abs(current_positions.get(order.symbol, 0))
    if equity > 0 and current_pos / equity > limits.max_position_per_symbol:
        if not order.reduce_only:
            return False, f"Position limit: {order.symbol} at {current_pos/equity:.1%}"

    # Total exposure
    total = sum(abs(v) for v in current_positions.values())
    if equity > 0 and total / equity > limits.max_total_exposure:
        if not order.reduce_only:
            return False, f"Exposure limit: {total/equity:.1%} >= {limits.max_total_exposure:.1%}"

    return True, "OK"
