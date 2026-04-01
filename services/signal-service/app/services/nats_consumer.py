from app.core.config import settings
from app.core.scoring import build_signal_response
from app.db.repository import signal_repository
from app.models.signal import FeatureSnapshot
from app.services.event_publisher import publisher
from app.services.strategy_registry_client import StrategyRegistryClient
from shared.events import JetStreamBus
from shared.persistence import RedisStore

strategy_client = StrategyRegistryClient(settings.strategy_registry_base_url)


class SignalConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._subscription = None

    async def start(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream("FEATURE_DATA", ["feature.updated.*"])
        self._subscription = await self._bus.subscribe(
            stream="FEATURE_DATA",
            subject="feature.updated.*",
            durable="signal-service-consumer",
            callback=self._handle,
            dlq_subject="feature.updated.dlq",
        )

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        data = payload["data"]
        asset = data["asset"]
        features = FeatureSnapshot.model_validate(data["feature"])
        asset_type = "crypto" if asset.endswith("USDT") or asset.endswith("KRW") else "stock"
        strategy = strategy_client.get_active_strategy(asset_type)
        evaluation = build_signal_response(
            asset=asset,
            features=features,
            threshold=settings.signal_threshold,
            asset_type=asset_type,
            strategy_id=None if strategy is None else strategy.get("id"),
            strategy_user_id=None if strategy is None else strategy.get("user_id"),
        )
        signal_repository.save(asset=asset, evaluation=evaluation)
        if evaluation.threshold_crossed:
            publisher.publish_threshold(asset=asset, asset_type=asset_type, evaluation=evaluation)


consumer = SignalConsumer()
