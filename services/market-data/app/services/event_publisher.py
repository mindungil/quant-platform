from app.core.config import settings
from app.models.candle import CandleUpdatedEvent
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
        await self._bus.ensure_stream(settings.jetstream_stream_name, ["market.candle.updated.*", "data.anomaly.detected.*"])

    async def close(self) -> None:
        await self._bus.close()

    async def publish_market_candle_async(self, asset: str, event: CandleUpdatedEvent) -> None:
        await self._bus.publish(
            f"market.candle.updated.{asset}",
            EventEnvelope(
                event_type="market.candle.updated",
                source="market-data",
                data=event.model_dump(mode="json"),
            ),
        )
        if event.anomaly_detected:
            await self._bus.publish(
                f"data.anomaly.detected.{asset}",
                EventEnvelope(
                    event_type="data.anomaly.detected",
                    source="market-data",
                    data=event.model_dump(mode="json"),
                ),
            )

    def publish_market_candle(self, asset: str, event: CandleUpdatedEvent) -> None:
        run_coro(self.publish_market_candle_async(asset=asset, event=event))


publisher = EventPublisher()
