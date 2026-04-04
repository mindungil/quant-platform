"""Outcome Reinforcement Consumer — 주문 체결 후 메모리 자동 업데이트.

NATS에서 order.filled 이벤트를 수신하면:
1. 해당 decision의 memory_refs를 조회
2. PnL 기반 trade_outcome 계산
3. 각 메모리 레코드에 결과 반영 (reinforce)

이를 통해 에이전트가 실시간으로 자신의 판단 결과를 학습.
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.db.repository import decision_repository
from app.services.memory_client import MemoryClient
from shared.events import JetStreamBus
from shared.persistence import RedisStore
from prometheus_client import Counter, Gauge

logger = logging.getLogger("outcome-consumer")

outcome_reinforcement_total = Counter(
    "outcome_reinforcement_total",
    "Total outcome reinforcement attempts",
    ["status"],
)
outcome_reinforcement_skipped_total = Counter(
    "outcome_reinforcement_skipped_total",
    "Total outcome reinforcements skipped",
)
outcome_reinforcement_pnl_total = Gauge(
    "outcome_reinforcement_pnl_total",
    "Cumulative PnL from reinforced outcomes",
)

memory_client = MemoryClient(settings.memory_service_base_url)


class OutcomeReinforcementConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )

    async def start(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(
            settings.execution_jetstream_stream,
            ["order.filled", "order.filled.dlq", "memory.reinforce.failed"],
        )
        await self._bus.subscribe(
            stream=settings.execution_jetstream_stream,
            subject="order.filled",
            durable="outcome-reinforcement-consumer",
            callback=self._handle,
            dlq_subject="order.filled.dlq",
        )
        logger.info("outcome_reinforcement_consumer_started")

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        """Process order.filled event → reinforce memory."""
        try:
            data = payload.get("data", {})
            correlation_id = data.get("correlation_id") or payload.get("correlation_id")
            order_id = data.get("order_id", "")
            asset = data.get("asset", "")
            side = data.get("side", "")
            fill_price = float(data.get("fill_price", 0) or data.get("price", 0))
            quantity = float(data.get("quantity", 0))
            pnl = float(data.get("pnl", 0))

            if not correlation_id:
                logger.debug("order_filled_no_correlation", extra={"order_id": order_id})
                outcome_reinforcement_skipped_total.inc()
                return

            # Find the decision that created this order
            decision = decision_repository.get_by_correlation_id(correlation_id)
            if not decision:
                logger.debug("order_filled_no_decision", extra={"correlation_id": correlation_id})
                outcome_reinforcement_skipped_total.inc()
                return

            decision_data = decision.get("payload", decision)
            memory_refs = decision_data.get("memory_refs", [])
            reference_price = float(decision_data.get("reference_price", 0))
            signal_score = float(decision_data.get("signal_score", 0))

            # Calculate trade outcome
            if pnl != 0:
                trade_outcome = pnl
            elif reference_price > 0 and fill_price > 0:
                if side.upper() == "BUY":
                    trade_outcome = (fill_price - reference_price) / reference_price
                else:
                    trade_outcome = (reference_price - fill_price) / reference_price
            else:
                trade_outcome = 0.0

            # Simple Sharpe-like metric: outcome / abs(signal) as efficiency
            outcome_sharpe = trade_outcome / max(abs(signal_score), 0.01)

            # Reinforce each linked memory record
            reinforced = 0
            for mem_id in memory_refs:
                success = False
                for attempt in range(1, 4):  # 3 attempts
                    try:
                        memory_client.reinforce(mem_id, trade_outcome, outcome_sharpe)
                        reinforced += 1
                        success = True
                        break
                    except Exception as exc:
                        if attempt < 3:
                            import time
                            time.sleep(1)
                        else:
                            logger.warning("reinforce_failed_after_retries", extra={
                                "memory_id": mem_id, "error": str(exc)[:100], "attempts": 3,
                            })
                if not success:
                    outcome_reinforcement_total.labels(status="failed").inc()

            # Also reinforce the decision's own memory record if it has one
            decision_memory_id = decision_data.get("decision_id")
            if decision_memory_id and decision_memory_id not in memory_refs:
                try:
                    memory_client.reinforce(decision_memory_id, trade_outcome, outcome_sharpe)
                    reinforced += 1
                except Exception as exc:
                    logger.warning("reinforce_decision_memory_failed", extra={
                        "memory_id": decision_memory_id,
                        "error": str(exc)[:100],
                        "correlation_id": correlation_id,
                    })
                    # Publish failure event for observability
                    try:
                        await self._bus.publish(
                            "memory.reinforce.failed",
                            __import__("shared.events", fromlist=["EventEnvelope"]).EventEnvelope(
                                event_type="memory.reinforce.failed",
                                source="outcome-consumer",
                                correlation_id=correlation_id,
                                data={
                                    "memory_id": decision_memory_id,
                                    "error": str(exc)[:100],
                                    "correlation_id": correlation_id,
                                },
                            ),
                        )
                    except Exception:
                        logger.debug("reinforce_failed_event_publish_error")

            logger.info("outcome_reinforced", extra={
                "correlation_id": correlation_id,
                "asset": asset,
                "trade_outcome": f"{trade_outcome:.4f}",
                "reinforced_count": reinforced,
            })
            outcome_reinforcement_total.labels(status="success").inc()
            outcome_reinforcement_pnl_total.inc(trade_outcome)

        except Exception as exc:
            logger.error("outcome_handle_error", extra={"error": str(exc)[:200]})


outcome_consumer = OutcomeReinforcementConsumer()
