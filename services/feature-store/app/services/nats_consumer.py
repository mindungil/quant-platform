from app.core.config import settings
from app.core.indicators import calculate_features
from app.db.repository import candle_repository, feature_repository
from app.models.feature import CandlePayload
from app.services.event_publisher import publisher
from shared.events import JetStreamBus
from shared.persistence import RedisStore


class FeatureStoreConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._subscription = None

    async def start(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream("MARKET_DATA", ["market.candle.updated.*", "data.anomaly.detected.*"])
        self._subscription = await self._bus.subscribe(
            stream="MARKET_DATA",
            subject="market.candle.updated.*",
            durable="feature-store-consumer",
            callback=self._handle,
            dlq_subject="market.candle.updated.dlq",
        )

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        data = payload["data"]
        asset = data["asset"]
        candle = CandlePayload.model_validate(data["candle"])
        candle_repository.add(asset, candle)
        feature = calculate_features(asset=asset, candles=candle_repository.get(asset))
        feature_repository.save(asset, feature)
        publisher.publish_feature(asset=asset, feature=feature)


consumer = FeatureStoreConsumer()
