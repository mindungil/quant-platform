import asyncio
import json

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.models.feature import FeatureResponse, FeatureUpdatedEvent


class EventPublisher:
    def __init__(self) -> None:
        self._client: NATS | None = None

    async def connect(self) -> None:
        if not settings.enable_nats:
            return
        self._client = NATS()
        await self._client.connect(settings.nats_url)

    async def close(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.drain()

    async def publish_feature_async(self, asset: str, feature: FeatureResponse) -> None:
        if self._client is None or not self._client.is_connected:
            return
        event = FeatureUpdatedEvent(
            asset=asset,
            subject=f"feature.updated.{asset}",
            feature=feature,
        )
        await self._client.publish(
            event.subject,
            json.dumps(event.model_dump(mode="json")).encode("utf-8"),
        )

    def publish_feature(self, asset: str, feature: FeatureResponse) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish_feature_async(asset=asset, feature=feature))


publisher = EventPublisher()
