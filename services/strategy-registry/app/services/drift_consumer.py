"""Drift Alert Consumer — listens for strategy.drift_alert events.

When drift is critical (score > threshold), transitions ACTIVE strategies to DEPRECATED.
"""
from __future__ import annotations

import logging
import os

from prometheus_client import Counter

from shared.events import JetStreamBus
from shared.persistence import RedisStore

logger = logging.getLogger("drift-consumer")

drift_deprecations_total = Counter(
    "drift_deprecations_total",
    "Total strategies deprecated due to drift",
    ["strategy_id"],
)

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STATISTICS_STREAM = os.getenv("STATISTICS_JETSTREAM_STREAM", "STATISTICS")


class DriftConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=NATS_URL,
            redis_store=RedisStore(REDIS_URL),
            enabled=True,
        )

    async def start(self) -> None:
        try:
            await self._bus.connect()
            await self._bus.ensure_stream(STATISTICS_STREAM, ["strategy.drift_alert", "strategy.drift_alert.dlq"])
            await self._bus.subscribe(
                stream=STATISTICS_STREAM,
                subject="strategy.drift_alert",
                durable="drift-deprecation-consumer",
                callback=self._handle,
                dlq_subject="strategy.drift_alert.dlq",
            )
            logger.info("drift_consumer_started")
        except Exception as exc:
            logger.warning("drift_consumer_start_failed: %s", exc)

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        """Process strategy.drift_alert — deprecate ACTIVE strategies with critical drift."""
        data = payload.get("data", {})
        strategy_id = data.get("strategy_id")
        drift_score = float(data.get("drift_score", 0))
        threshold = float(data.get("threshold", 0.1))

        if not strategy_id or strategy_id == "default":
            return

        if drift_score <= threshold:
            return

        try:
            from app.db.repository import strategy_repository

            strategy = strategy_repository.get(strategy_id)
            if strategy is None:
                logger.debug("drift_strategy_not_found", extra={"strategy_id": strategy_id})
                return

            if strategy.status != "ACTIVE":
                logger.debug("drift_strategy_not_active", extra={
                    "strategy_id": strategy_id, "status": strategy.status,
                })
                return

            strategy_repository.update_status(strategy_id, "DEPRECATED")
            drift_deprecations_total.labels(strategy_id=strategy_id).inc()
            logger.info("strategy_deprecated_by_drift", extra={
                "strategy_id": strategy_id,
                "drift_score": drift_score,
                "threshold": threshold,
            })
        except Exception as exc:
            logger.warning("drift_deprecation_failed", extra={
                "strategy_id": strategy_id,
                "error": str(exc)[:200],
            })


drift_consumer = DriftConsumer()
