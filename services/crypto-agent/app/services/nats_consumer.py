import asyncio

from app.core.config import settings
from app.core.engine import run_decision_loop
from app.services.strategy_client import StrategyClient
from shared.events import JetStreamBus
from shared.logging import get_logger
from shared.persistence import RedisStore

logger = get_logger("crypto-agent")

_strategy_client = StrategyClient(settings.strategy_registry_base_url)


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
        """Dispatch a signal-threshold event through BOTH lanes:

        - Agent core lane: one run with the event's strategy_user_id (or
          default). This uses the validated engine strategy.
        - User template lane: one run per enabled subscription across all
          users who have opted in. Each run is tagged with the user_id so
          the graph picks up that user's ACTIVE strategy — subscriptions
          live in a separate table but the per-user run still exercises
          the same pipeline with the correct ownership.
        """
        event_data = payload["data"]
        asset = event_data["asset"]
        correlation_id = payload.get("correlation_id")

        loop = asyncio.get_event_loop()

        # Agent core lane (existing behavior)
        agent_user = event_data.get("strategy_user_id")
        try:
            await loop.run_in_executor(
                None,
                lambda: run_decision_loop(
                    asset, user_id=agent_user, correlation_id=correlation_id,
                ),
            )
        except Exception:
            logger.exception("agent_core_lane_failed", extra={"asset": asset})

        # Template lane — fan out per subscribed user
        try:
            subs = _strategy_client.list_all_enabled_subscriptions("crypto")
        except Exception:
            subs = []

        seen_users: set[str] = set()
        for sub in subs:
            uid = sub.get("user_id")
            if not uid or uid in seen_users:
                continue
            seen_users.add(uid)
            sub_corr = f"{correlation_id or asset}:tpl:{uid}"
            try:
                await loop.run_in_executor(
                    None,
                    lambda u=uid, c=sub_corr: run_decision_loop(
                        asset, user_id=u, correlation_id=c,
                    ),
                )
            except Exception:
                logger.exception(
                    "user_template_lane_failed",
                    extra={"asset": asset, "user_id": uid},
                )


consumer = CryptoAgentConsumer()
