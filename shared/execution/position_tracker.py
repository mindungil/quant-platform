"""Position tracker — reconciles target vs actual positions.

Compares signal-generated target positions with exchange-reported
actual positions. Outputs a list of TradeOrders needed to align.
Every reconcile() call also writes a structured JSONL audit record so
post-hoc forensics can answer "what did we think the book was, what did
it actually look like, and what did we send" for any timestamp.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.execution.connector import ExchangeConnector
from shared.execution.risk_limits import TradeOrder

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_DIR = Path("data/logs/reconciliation")


@dataclass
class ReconciliationResult:
    """Result of comparing target vs actual positions."""
    target_positions: dict[str, float]
    actual_positions: dict[str, float]
    orders_needed: list[TradeOrder]
    skipped: list[dict[str, Any]]  # orders skipped (too small, etc.)


class PositionTracker:
    """Tracks and reconciles target vs actual positions."""

    def __init__(
        self,
        connector: ExchangeConnector,
        min_trade_notional: float = 10.0,
        position_precision: dict[str, int] | None = None,
        audit_log_dir: Path | str | None = None,
    ) -> None:
        self.connector = connector
        self.min_trade_notional = min_trade_notional
        self.position_precision = position_precision or {}
        self.audit_log_dir = Path(audit_log_dir) if audit_log_dir else DEFAULT_AUDIT_DIR

    def reconcile(
        self,
        target_positions: dict[str, float],
        prices: dict[str, float],
    ) -> ReconciliationResult:
        """Compare target positions with exchange and compute needed orders.

        Args:
            target_positions: {symbol: target_quantity} (signed, in base units)
            prices: {symbol: current_price}

        Returns:
            ReconciliationResult with list of TradeOrders.
        """
        actual = self.connector.get_positions()
        orders: list[TradeOrder] = []
        skipped: list[dict[str, Any]] = []

        all_symbols = set(target_positions.keys()) | set(actual.keys())

        for symbol in sorted(all_symbols):
            target_qty = target_positions.get(symbol, 0.0)
            actual_qty = actual.get(symbol, 0.0)
            delta = target_qty - actual_qty

            if abs(delta) < 1e-8:
                continue

            price = prices.get(symbol, 0)
            if price <= 0:
                skipped.append({"symbol": symbol, "reason": "no price"})
                continue

            notional = abs(delta) * price
            if notional < self.min_trade_notional:
                skipped.append({
                    "symbol": symbol,
                    "reason": f"notional ${notional:.2f} < ${self.min_trade_notional}",
                })
                continue

            # Round quantity to symbol precision
            precision = self.position_precision.get(symbol, 3)
            rounded_qty = round(abs(delta), precision)
            if rounded_qty <= 0:
                continue

            # Re-check notional after rounding — stepSize rounding can
            # shrink the order below minNotional and cause exchange rejects.
            # (Observed: ETH delta 0.00249 → rounded 0.002 → $4.64 < $5.)
            rounded_notional = rounded_qty * price
            if rounded_notional < self.min_trade_notional:
                skipped.append({
                    "symbol": symbol,
                    "reason": f"post-round notional ${rounded_notional:.2f} < ${self.min_trade_notional}",
                })
                continue

            side = "BUY" if delta > 0 else "SELL"
            reduce_only = (
                (actual_qty > 0 and delta < 0) or
                (actual_qty < 0 and delta > 0)
            )

            orders.append(TradeOrder(
                symbol=symbol,
                side=side,
                quantity=rounded_qty,
                order_type="MARKET",
                reduce_only=reduce_only,
            ))

            logger.info(
                "Reconcile %s: actual=%.4f target=%.4f → %s %.4f (notional=$%.0f)",
                symbol, actual_qty, target_qty, side, rounded_qty, notional,
            )

        result = ReconciliationResult(
            target_positions=target_positions,
            actual_positions=actual,
            orders_needed=orders,
            skipped=skipped,
        )
        self._audit(result, prices)
        return result

    def sync_from_exchange(self) -> dict[str, float]:
        """Fetch current positions from exchange."""
        return self.connector.get_positions()

    def _audit(self, result: ReconciliationResult, prices: dict[str, float]) -> None:
        """Append one JSONL line per reconcile call.

        File rotated daily by date suffix so historical audits are easy
        to filter without reading the whole log.
        """
        try:
            from shared.execution.mode import get_execution_mode
            mode_ctx = get_execution_mode()
            mode = mode_ctx.mode.value
        except Exception:
            mode = "unknown"

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "n_orders": len(result.orders_needed),
            "n_skipped": len(result.skipped),
            "target": {k: round(v, 6) for k, v in result.target_positions.items()},
            "actual": {k: round(v, 6) for k, v in result.actual_positions.items()},
            "prices": {k: round(v, 4) for k, v in prices.items() if v > 0},
            "orders": [
                {
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": o.quantity,
                    "reduce_only": o.reduce_only,
                }
                for o in result.orders_needed
            ],
            "skipped": result.skipped,
        }

        try:
            self.audit_log_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = self.audit_log_dir / f"reconcile-{day}.jsonl"
            with open(path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.warning("reconciliation audit write failed: %s", e)
