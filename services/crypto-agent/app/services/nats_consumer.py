from app.core.config import settings
from app.core.engine import run_decision_loop
from shared.events import JetStreamBus
from shared.persistence import RedisStore


class CryptoAgentConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._subscription = None

    async def start(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(settings.jetstream_stream_name, ["signal.threshold.crossed.*"])
        self._subscription = await self._bus.subscribe(
            stream=settings.jetstream_stream_name,
            subject="signal.threshold.crossed.crypto",
            durable="crypto-agent-consumer",
            callback=self._handle,
            dlq_subject="signal.threshold.crossed.crypto.dlq",
        )

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        run_decision_loop(payload["data"]["asset"])


consumer = CryptoAgentConsumer()
