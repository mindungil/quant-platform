import json

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.core.engine import run_decision_loop


class CryptoAgentConsumer:
    def __init__(self) -> None:
        self._client: NATS | None = None
        self._subscription = None

    async def start(self) -> None:
        if not settings.enable_nats:
            return
        self._client = NATS()
        await self._client.connect(settings.nats_url)
        self._subscription = await self._client.subscribe("signal.threshold.crossed.crypto", cb=self._handle)

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
        if self._client is not None and self._client.is_connected:
            await self._client.drain()

    async def _handle(self, message) -> None:
        payload = json.loads(message.data.decode("utf-8"))
        run_decision_loop(payload["asset"])


consumer = CryptoAgentConsumer()
