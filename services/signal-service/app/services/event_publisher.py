import asyncio
import json

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.models.signal import SignalEvaluationResponse, SignalThresholdEvent


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

    async def publish_threshold_async(
        self, asset: str, asset_type: str, evaluation: SignalEvaluationResponse
    ) -> None:
        if self._client is None or not self._client.is_connected:
            return
        event = SignalThresholdEvent(
            asset=asset,
            asset_type=asset_type,
            subject=f"signal.threshold.crossed.{asset_type}",
            evaluation=evaluation,
        )
        await self._client.publish(
            event.subject,
            json.dumps(event.model_dump(mode="json")).encode("utf-8"),
        )

    def publish_threshold(self, asset: str, asset_type: str, evaluation: SignalEvaluationResponse) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish_threshold_async(asset=asset, asset_type=asset_type, evaluation=evaluation))


publisher = EventPublisher()
