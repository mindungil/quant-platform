from app.core.config import settings
from app.core.engine import process_order
from app.models.order import OrderRequest
from shared.events import JetStreamBus
from shared.persistence import RedisStore


class OrderConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._subscription = None

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

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        order_payload = payload["data"].get("order_request")
        if order_payload is None:
            return
        process_order(OrderRequest.model_validate(order_payload))


consumer = OrderConsumer()
