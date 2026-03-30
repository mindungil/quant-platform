import json

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.models.candle import CandleUpdatedEvent


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

    async def publish_market_candle_async(self, asset: str, event: CandleUpdatedEvent) -> None:
        if self._client is None or not self._client.is_connected:
            return
        await self._client.publish(
            f"market.candle.updated.{asset}",
            json.dumps(event.model_dump(mode="json")).encode("utf-8"),
        )

    def publish_market_candle(self, asset: str, event: CandleUpdatedEvent) -> None:
        # API routes can call this without caring whether NATS is enabled.
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish_market_candle_async(asset=asset, event=event))


publisher = EventPublisher()
