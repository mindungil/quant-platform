import asyncio

from app.core.config import settings
from app.models.signal import SignalEvaluationResponse, SignalThresholdEvent
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
        await self._bus.ensure_stream(settings.jetstream_stream_name, ["signal.threshold.crossed.*", "signal.threshold.crossed.*.dlq"])

    async def close(self) -> None:
        await self._bus.close()

    async def publish_threshold_async(
        self, asset: str, asset_type: str, evaluation: SignalEvaluationResponse
    ) -> None:
        event = SignalThresholdEvent(
            asset=asset,
            asset_type=asset_type,
            subject=f"signal.threshold.crossed.{asset_type}",
            evaluation=evaluation,
        )
        await self._bus.publish(
            event.subject,
            EventEnvelope(
                event_type="signal.threshold.crossed",
                source="signal-service",
                data=event.model_dump(mode="json"),
            ),
        )

    def publish_threshold(self, asset: str, asset_type: str, evaluation: SignalEvaluationResponse) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish_threshold_async(asset=asset, asset_type=asset_type, evaluation=evaluation))


publisher = EventPublisher()
