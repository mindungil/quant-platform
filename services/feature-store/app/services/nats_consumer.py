import json

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.core.indicators import calculate_features
from app.db.repository import candle_repository, feature_repository
from app.models.feature import CandlePayload
from app.services.event_publisher import publisher


class FeatureStoreConsumer:
    def __init__(self) -> None:
        self._client: NATS | None = None
        self._subscription = None

    async def start(self) -> None:
        if not settings.enable_nats:
            return
        self._client = NATS()
        await self._client.connect(settings.nats_url)
        self._subscription = await self._client.subscribe("market.candle.updated.*", cb=self._handle)

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
        if self._client is not None and self._client.is_connected:
            await self._client.drain()

    async def _handle(self, message) -> None:
        payload = json.loads(message.data.decode("utf-8"))
        asset = payload["asset"]
        candle = CandlePayload.model_validate(payload["candle"])
        candle_repository.add(asset, candle)
        feature = calculate_features(asset=asset, candles=candle_repository.get(asset))
        feature_repository.save(asset, feature)
        publisher.publish_feature(asset=asset, feature=feature)


consumer = FeatureStoreConsumer()
