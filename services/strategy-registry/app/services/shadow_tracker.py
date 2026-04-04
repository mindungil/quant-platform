"""Shadow Strategy Tracker — listens for order.filled events and updates shadow metrics.

When a shadow order fills, updates the strategy's shadow_metrics:
- Increments trade count
- Recalculates running Sharpe ratio and win_rate
- Accumulates PnL
"""
from __future__ import annotations

import logging
import math
import os

from prometheus_client import Counter

from shared.events import JetStreamBus
from shared.persistence import RedisStore

logger = logging.getLogger("shadow-tracker")

shadow_trades_processed_total = Counter(
    "shadow_trades_processed_total",
    "Total shadow trades processed for metrics update",
    ["strategy_id"],
)

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
EXECUTION_STREAM = os.getenv("EXECUTION_JETSTREAM_STREAM", "EXECUTION")


class ShadowTracker:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=NATS_URL,
            redis_store=RedisStore(REDIS_URL),
            enabled=True,
        )
        # In-memory running stats per strategy for incremental Sharpe calculation
        self._running: dict[str, dict] = {}

    async def start(self) -> None:
        try:
            await self._bus.connect()
            await self._bus.ensure_stream(EXECUTION_STREAM, ["order.filled", "order.filled.dlq"])
            await self._bus.subscribe(
                stream=EXECUTION_STREAM,
                subject="order.filled",
                durable="shadow-tracker-consumer",
                callback=self._handle,
                dlq_subject="order.filled.dlq",
            )
            logger.info("shadow_tracker_started")
        except Exception as exc:
            logger.warning("shadow_tracker_start_failed: %s", exc)

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        """Process order.filled — update shadow metrics if the order is shadow mode."""
        data = payload.get("data", {})

        # Only process shadow orders
        if not data.get("shadow_mode") and not data.get("shadow"):
            return

        strategy_id = data.get("strategy_id")
        if not strategy_id:
            return

        pnl = float(data.get("pnl", 0))
        fill_price = float(data.get("fill_price", 0) or data.get("price", 0))
        reference_price = float(data.get("reference_price", 0))

        # Calculate PnL from prices if not provided
        if pnl == 0 and reference_price > 0 and fill_price > 0:
            side = data.get("side", "").upper()
            if side == "BUY":
                pnl = (fill_price - reference_price) / reference_price
            elif side == "SELL":
                pnl = (reference_price - fill_price) / reference_price

        # Update running stats
        if strategy_id not in self._running:
            self._running[strategy_id] = {
                "trades": [],
                "total_pnl": 0.0,
                "wins": 0,
                "count": 0,
            }
        stats = self._running[strategy_id]
        stats["trades"].append(pnl)
        stats["total_pnl"] += pnl
        stats["count"] += 1
        if pnl > 0:
            stats["wins"] += 1

        # Calculate running metrics
        trade_count = stats["count"]
        win_rate = stats["wins"] / trade_count if trade_count > 0 else 0.0

        # Simple Sharpe: mean / std of trade returns
        sharpe = 0.0
        if trade_count > 1:
            trades = stats["trades"]
            mean_ret = sum(trades) / len(trades)
            variance = sum((t - mean_ret) ** 2 for t in trades) / (len(trades) - 1)
            std_dev = math.sqrt(variance) if variance > 0 else 0.001
            sharpe = mean_ret / std_dev

        # Max drawdown from cumulative returns
        max_dd = 0.0
        cumulative = 0.0
        peak = 0.0
        for t in stats["trades"]:
            cumulative += t
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        # Update strategy-registry via repository
        try:
            from app.db.repository import strategy_repository
            strategy_repository.update_shadow_metrics(strategy_id, {
                "trade_count": 1,  # incremental
                "pnl": pnl,  # incremental
                "sharpe": round(sharpe, 4),
                "win_rate": round(win_rate, 4),
                "max_drawdown": round(max_dd, 4),
            })
            shadow_trades_processed_total.labels(strategy_id=strategy_id).inc()
            logger.info("shadow_metrics_updated", extra={
                "strategy_id": strategy_id,
                "trade_count": trade_count,
                "sharpe": round(sharpe, 4),
                "win_rate": round(win_rate, 4),
                "pnl": round(pnl, 6),
            })
        except Exception as exc:
            logger.warning("shadow_metrics_update_failed", extra={
                "strategy_id": strategy_id,
                "error": str(exc)[:200],
            })


shadow_tracker = ShadowTracker()
