"""Order executor with risk checks, retry logic, and dry-run mode.

Executes a list of TradeOrders against an exchange connector,
applying pre-trade risk checks and retry logic.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.execution.connector import ExchangeConnector
from shared.execution.risk_limits import (
    ExecutionResult,
    OrderResult,
    RiskLimits,
    TradeOrder,
    check_pre_trade,
)

logger = logging.getLogger(__name__)


class OrderExecutor:
    """Executes orders with risk management."""

    def __init__(
        self,
        connector: ExchangeConnector,
        risk_limits: RiskLimits | None = None,
        dry_run: bool = True,
        log_dir: str = "data/logs/execution",
        max_log_files: int = 500,
    ) -> None:
        self.connector = connector
        self.limits = risk_limits or RiskLimits()
        self.dry_run = dry_run
        self.log_dir = Path(log_dir)
        self._max_log_files = max_log_files
        self._daily_turnover = 0.0
        self._turnover_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def execute(
        self,
        orders: list[TradeOrder],
        equity: float,
        current_positions: dict[str, float],
        current_drawdown: float = 0.0,
        prices: dict[str, float] | None = None,
    ) -> ExecutionResult:
        """Execute orders with pre-trade risk checks.

        Args:
            orders: List of TradeOrders to execute
            equity: Current account equity in USD
            current_positions: {symbol: notional_value}
            current_drawdown: Current peak-to-trough DD (0 to 1)
            prices: {symbol: price} for notional calculation

        Returns:
            ExecutionResult with per-order results.
        """
        # Auto-reset daily turnover at day boundary
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._turnover_date:
            logger.info("Daily turnover reset: %s → %s (was %.4f)",
                        self._turnover_date, today, self._daily_turnover)
            self._daily_turnover = 0.0
            self._turnover_date = today

        prices = prices or {}
        results: list[OrderResult] = []
        total_notional = 0.0
        filled = 0
        failed = 0

        for order in orders:
            # Estimate price for risk check
            if order.price:
                est_price = order.price
            else:
                est_price = prices.get(order.symbol, 0)

            # Create a version with estimated price for risk check
            check_order = TradeOrder(
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=est_price,
                reduce_only=order.reduce_only,
            )

            # Pre-trade risk check
            passed, reason = check_pre_trade(
                check_order, self.limits, equity,
                current_positions, self._daily_turnover,
                current_drawdown,
            )

            if not passed:
                logger.warning("BLOCKED %s %s %.4f %s: %s",
                               order.side, order.symbol, order.quantity,
                               order.order_type, reason)
                results.append(OrderResult(
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    filled_quantity=0,
                    avg_price=0,
                    status="REJECTED",
                    error=reason,
                ))
                failed += 1
                continue

            # Execute or dry-run
            if self.dry_run:
                logger.info("[DRY-RUN] %s %s %.4f %s @ ~$%.2f",
                            order.side, order.symbol, order.quantity,
                            order.order_type, est_price)
                result = OrderResult(
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    filled_quantity=order.quantity,
                    avg_price=est_price,
                    status="DRY_RUN",
                )
            else:
                result = self._execute_with_retry(order)

            results.append(result)
            notional = result.filled_quantity * result.avg_price
            total_notional += notional

            if result.status in ("FILLED", "DRY_RUN"):
                filled += 1
                if equity > 0:
                    self._daily_turnover += notional / equity
            else:
                failed += 1

        exec_result = ExecutionResult(
            orders_sent=len(orders),
            orders_filled=filled,
            orders_failed=failed,
            total_notional=total_notional,
            results=results,
        )

        self._log_execution(exec_result)
        return exec_result

    def _execute_with_retry(
        self, order: TradeOrder, max_retries: int = 3,
    ) -> OrderResult:
        """Execute a single order with retry logic."""
        for attempt in range(max_retries):
            if order.order_type == "MARKET":
                result = self.connector.place_market_order(
                    order.symbol, order.side, order.quantity,
                )
            else:
                result = self.connector.place_limit_order(
                    order.symbol, order.side, order.quantity, order.price or 0,
                )

            if result.status in ("FILLED", "PARTIALLY_FILLED"):
                return result

            logger.warning("Order attempt %d/%d failed: %s — %s",
                           attempt + 1, max_retries, result.status, result.error)
            time.sleep(1 * (attempt + 1))

        return result

    def _log_execution(self, result: ExecutionResult) -> None:
        """Log execution result to disk."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = self.log_dir / f"exec_{ts}.json"
        data = {
            "timestamp": ts,
            "dry_run": self.dry_run,
            "orders_sent": result.orders_sent,
            "orders_filled": result.orders_filled,
            "orders_failed": result.orders_failed,
            "total_notional": result.total_notional,
            "results": [
                {
                    "symbol": r.symbol,
                    "side": r.side,
                    "quantity": r.quantity,
                    "filled": r.filled_quantity,
                    "avg_price": r.avg_price,
                    "status": r.status,
                    "error": r.error,
                }
                for r in result.results
            ],
        }
        with open(log_path, "w") as f:
            json.dump(data, f, indent=2)

        # Rotate old log files to prevent unbounded disk growth
        self._rotate_logs()

    def _rotate_logs(self) -> None:
        """Keep only the most recent max_log_files execution logs."""
        try:
            logs = sorted(self.log_dir.glob("exec_*.json"))
            if len(logs) > self._max_log_files:
                for old in logs[: len(logs) - self._max_log_files]:
                    old.unlink()
        except Exception:
            pass

    def reset_daily_turnover(self) -> None:
        """Reset daily turnover counter (call at start of new trading day)."""
        self._daily_turnover = 0.0
