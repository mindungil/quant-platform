from app.core.config import settings
from app.models.order import OrderRequest, OrderResponse
from shared.asyncio_utils import run_coro
from shared.events import EventEnvelope, JetStreamBus
from shared.logging import get_logger
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus

logger = get_logger("order-service")


class EventPublisher:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._realtime = RealtimeBus(RedisStore(settings.redis_url), replay_limit=settings.realtime_replay_limit)

    async def connect(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(
            settings.execution_jetstream_stream,
            [
                "agent.crypto.action",
                "agent.crypto.action.dlq",
                "risk.triggered",
                "risk.triggered.dlq",
                "order.created",
                "order.created.dlq",
                "order.filled",
                "order.filled.dlq",
                "order.cancelled",
                "order.cancelled.dlq",
                "portfolio.updated",
                "statistics.updated",
            ],
        )

    async def close(self) -> None:
        await self._bus.close()

    async def publish_order_created_async(self, payload: OrderRequest, order_id: str) -> None:
        body = {
            "order_id": order_id,
            "asset": payload.asset,
            "exchange": payload.exchange,
            "side": payload.side,
            "quantity": payload.quantity,
            "requested_notional": payload.requested_notional,
            "shadow_mode": payload.shadow_mode,
            "strategy_id": payload.strategy_id,
        }
        await self._bus.publish(
            "order.created",
            EventEnvelope(
                event_type="order.created",
                source="order-service",
                correlation_id=payload.correlation_id,
                user_id=payload.user_id,
                data=body,
            ),
        )
        logger.info(
            "order_created",
            extra={
                "service": "order-service",
                "correlation_id": payload.correlation_id,
                "user_id": payload.user_id,
                "event_type": "order.created",
            },
        )

    def publish_order_created(self, payload: OrderRequest, order_id: str) -> None:
        run_coro(self.publish_order_created_async(payload, order_id))

    async def publish_order_filled_async(self, payload: OrderRequest, response: OrderResponse) -> None:
        event = response.model_dump(mode="json")
        await self._bus.publish(
            "order.filled",
            EventEnvelope(
                event_type="order.filled",
                source="order-service",
                correlation_id=payload.correlation_id,
                user_id=payload.user_id,
                data=event,
            ),
        )
        self._realtime.publish(
            event_type="order.filled",
            source="order-service",
            user_id=payload.user_id,
            correlation_id=payload.correlation_id,
            data=event,
        )
        logger.info(
            "order_filled",
            extra={
                "service": "order-service",
                "correlation_id": payload.correlation_id,
                "user_id": payload.user_id,
                "event_type": "order.filled",
            },
        )

    def publish_order_filled(self, payload: OrderRequest, response: OrderResponse) -> None:
        run_coro(self.publish_order_filled_async(payload, response))

    async def publish_risk_triggered_async(
        self,
        *,
        payload: OrderRequest,
        reason: str,
        level: str,
        requested_notional: float,
    ) -> None:
        body = {
            "asset": payload.asset,
            "exchange": payload.exchange,
            "level": level,
            "reason": reason,
            "requested_notional": requested_notional,
        }
        await self._bus.publish(
            "risk.triggered",
            EventEnvelope(
                event_type="risk.triggered",
                source="order-service",
                correlation_id=payload.correlation_id,
                user_id=payload.user_id,
                data=body,
            ),
        )
        self._realtime.publish(
            event_type="risk.triggered",
            source="order-service",
            user_id=payload.user_id,
            correlation_id=payload.correlation_id,
            data=body,
        )

    async def publish_order_cancelled_async(self, order_id: str, user_id: str, correlation_id: str | None = None) -> None:
        body = {"order_id": order_id, "user_id": user_id, "status": "CANCELLED"}
        await self._bus.publish(
            "order.cancelled",
            EventEnvelope(
                event_type="order.cancelled",
                source="order-service",
                correlation_id=correlation_id,
                user_id=user_id,
                data=body,
            ),
        )
        self._realtime.publish(
            event_type="order.cancelled",
            source="order-service",
            user_id=user_id,
            correlation_id=correlation_id,
            data=body,
        )
        logger.info(
            "order_cancelled",
            extra={
                "service": "order-service",
                "correlation_id": correlation_id,
                "user_id": user_id,
                "event_type": "order.cancelled",
            },
        )

    def publish_order_cancelled(self, order_id: str, user_id: str, correlation_id: str | None = None) -> None:
        run_coro(self.publish_order_cancelled_async(order_id, user_id, correlation_id))

    def publish_risk_triggered(self, *, payload: OrderRequest, reason: str, level: str, requested_notional: float) -> None:
        run_coro(
            self.publish_risk_triggered_async(
                payload=payload,
                reason=reason,
                level=level,
                requested_notional=requested_notional,
            )
        )


publisher = EventPublisher()
