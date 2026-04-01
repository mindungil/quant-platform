from app.core.config import settings
from app.models.feature import FeatureResponse, FeatureUpdatedEvent
from shared.asyncio_utils import run_coro
from shared.events import EventEnvelope, JetStreamBus
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus


class EventPublisher:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._realtime = RealtimeBus(RedisStore(settings.redis_url))

    async def connect(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(settings.jetstream_stream_name, ["feature.updated.*", "feature.updated.*.dlq"])

    async def close(self) -> None:
        await self._bus.close()

    async def publish_feature_async(self, asset: str, feature: FeatureResponse) -> None:
        event = FeatureUpdatedEvent(
            asset=asset,
            subject=f"feature.updated.{asset}",
            feature=feature,
        )
        await self._bus.publish(
            event.subject,
            EventEnvelope(
                event_type="feature.updated",
                source="feature-store",
                data=event.model_dump(mode="json"),
            ),
        )
        self._realtime.publish(
            event_type="feature.updated",
            source="feature-store",
            data=event.model_dump(mode="json"),
        )

    def publish_feature(self, asset: str, feature: FeatureResponse) -> None:
        run_coro(self.publish_feature_async(asset=asset, feature=feature))


publisher = EventPublisher()
