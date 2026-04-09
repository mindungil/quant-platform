from app.core.config import settings
from app.core.engine import process_order
from app.core.protection import protection_manager
from app.models.order import OrderRequest
from shared.events import JetStreamBus
from shared.logging import get_logger
from shared.persistence import RedisStore

logger = get_logger("order-service")

MARKET_DATA_STREAM = "MARKET_DATA"


class OrderConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._market_bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._subscription = None
        self._market_subscription = None

    async def start(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(
            settings.execution_jetstream_stream,
            ["agent.crypto.action", "risk.triggered", "order.created", "order.filled", "portfolio.updated", "statistics.updated"],
        )
        self._subscription = await self._bus.subscribe(
            stream=settings.execution_jetstream_stream,
            subject="agent.crypto.action",
            durable="order-service-consumer",
            callback=self._handle,
            dlq_subject="agent.crypto.action.dlq",
        )

        # Subscribe to market candle updates for protection trigger checks
        await self._market_bus.connect()
        try:
            await self._market_bus.ensure_stream(
                MARKET_DATA_STREAM,
                ["market.candle.updated.*"],
            )
            self._market_subscription = await self._market_bus.subscribe(
                stream=MARKET_DATA_STREAM,
                subject="market.candle.updated.*",
                durable="order-service-protection-consumer",
                callback=self._handle_candle,
                dlq_subject="market.candle.updated.dlq",
            )
        except Exception:
            logger.warning(
                "market_candle_subscription_failed",
                extra={"service": "order-service", "event_type": "nats.subscription.failed"},
            )

    async def stop(self) -> None:
        await self._bus.close()
        await self._market_bus.close()

    async def _handle(self, payload: dict) -> None:
        order_payload = payload["data"].get("order_request")
        if order_payload is None:
            return
        process_order(OrderRequest.model_validate(order_payload))

    async def _handle_candle(self, payload: dict) -> None:
        """Process market candle events and check protection triggers."""
        try:
            data = payload.get("data", {})
            candle = data.get("candle", {})
            asset = data.get("asset")
            close_price = candle.get("close")

            if asset is None or close_price is None:
                return

            triggered = protection_manager.check_triggers(asset, float(close_price))
            for t in triggered:
                logger.info(
                    "protection_triggered_by_candle",
                    extra={
                        "service": "order-service",
                        "order_id": t.order_id,
                        "trigger_type": t.trigger_type,
                        "trigger_price": t.trigger_price,
                        "current_price": close_price,
                        "asset": asset,
                    },
                )
        except Exception:
            logger.exception(
                "candle_protection_check_failed",
                extra={"service": "order-service", "event_type": "protection.check.failed"},
            )


consumer = OrderConsumer()
