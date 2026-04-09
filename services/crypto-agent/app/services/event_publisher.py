from app.core.config import settings
from app.models.agent import DecisionRecord
from shared.asyncio_utils import run_coro
from shared.events import EventEnvelope, JetStreamBus
from shared.persistence import RedisStore


class EventPublisher:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )

    async def connect(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(
            settings.execution_jetstream_stream,
            [
                "agent.crypto.action",
                "agent.crypto.action.dlq",
                "order.created",
                "order.filled",
                "risk.triggered",
                "portfolio.updated",
                "statistics.updated",
            ],
        )

    async def close(self) -> None:
        await self._bus.close()

    async def publish_agent_action_async(self, decision: DecisionRecord, order_request: dict) -> None:
        await self._bus.publish(
            "agent.crypto.action",
            EventEnvelope(
                event_type="agent.crypto.action",
                source="crypto-agent",
                correlation_id=decision.correlation_id,
                user_id=decision.user_id,
                data={
                    "decision": decision.model_dump(mode="json"),
                    "order_request": order_request,
                },
            ),
        )

    def publish_agent_action(self, decision: DecisionRecord, order_request: dict) -> None:
        run_coro(self.publish_agent_action_async(decision, order_request))


publisher = EventPublisher()
