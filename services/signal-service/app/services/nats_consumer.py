import json

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.core.scoring import build_signal_response
from app.db.repository import signal_repository
from app.models.signal import FeatureSnapshot
from app.services.event_publisher import publisher


class SignalConsumer:
    def __init__(self) -> None:
        self._client: NATS | None = None
        self._subscription = None

    async def start(self) -> None:
        if not settings.enable_nats:
            return
        self._client = NATS()
        await self._client.connect(settings.nats_url)
        self._subscription = await self._client.subscribe("feature.updated.*", cb=self._handle)

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
        if self._client is not None and self._client.is_connected:
            await self._client.drain()

    async def _handle(self, message) -> None:
        payload = json.loads(message.data.decode("utf-8"))
        features = FeatureSnapshot.model_validate(payload["feature"])
        evaluation = build_signal_response(
            asset=payload["asset"],
            features=features,
            threshold=settings.signal_threshold,
        )
        signal_repository.save(asset=payload["asset"], evaluation=evaluation)
        if evaluation.threshold_crossed:
            publisher.publish_threshold(asset=payload["asset"], asset_type="crypto", evaluation=evaluation)


consumer = SignalConsumer()
