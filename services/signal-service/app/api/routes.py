from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from app.core.config import settings
from app.core.scoring import build_signal_response
from app.db.repository import signal_repository
from app.services.event_publisher import publisher
from app.services.external_data_client import ExternalDataClient
from app.services.feature_store_client import FeatureStoreClient
from app.services.strategy_registry_client import StrategyRegistryClient
from shared.health import check_redis, check_sql, check_tcp, health_payload

signals_evaluated_total = Counter(
    "signals_evaluated_total",
    "Total signals evaluated",
    ["direction"],
)

router = APIRouter()
client = FeatureStoreClient(base_url=settings.feature_store_base_url)
external_client = ExternalDataClient(base_url=settings.external_data_service_base_url)
strategy_client = StrategyRegistryClient(settings.strategy_registry_base_url)


@router.get("/health")
def health() -> dict:
    return health_payload(
        "signal-service",
        {
            "timescaledb": check_sql("timescaledb", settings.timescale_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/signals/evaluate/{asset}")
def evaluate_signal(asset: str, x_user_id: str | None = Header(default=None)):
    features = client.get_latest_features(asset)
    external_context = external_client.get_external_context(asset)
    asset_type = "crypto" if asset.endswith("USDT") or asset.endswith("KRW") else "stock"
    strategy = strategy_client.get_active_strategy(asset_type, user_id=x_user_id)
    thresholds = {} if strategy is None else strategy.get("thresholds", {})
    evaluation = build_signal_response(
        asset=asset,
        features=features,
        threshold=settings.signal_threshold,
        entry_threshold=thresholds.get("entry"),
        exit_threshold=thresholds.get("exit"),
        asset_type=asset_type,
        strategy_id=None if strategy is None else strategy.get("id"),
        strategy_user_id=None if strategy is None else strategy.get("user_id"),
        external_context=external_context,
        external_signal_weight=settings.external_signal_weight,
    )
    signal_repository.save(asset=asset, evaluation=evaluation)
    signals_evaluated_total.labels(direction=evaluation.direction).inc()
    if evaluation.threshold_crossed:
        publisher.publish_threshold(asset=asset, asset_type=asset_type, evaluation=evaluation)
    return evaluation


@router.get("/signals/{asset}/latest")
def get_latest_signal(asset: str, x_user_id: str | None = Header(default=None)):
    evaluation = signal_repository.get_latest(asset, user_id=x_user_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    return evaluation


@router.get("/signals")
def list_latest_signals(x_user_id: str | None = Header(default=None)):
    return signal_repository.list_latest(user_id=x_user_id)


@router.get("/signals/{asset}/history")
def get_signal_history(
    asset: str,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    x_user_id: str | None = Header(default=None),
):
    history = signal_repository.get_history(asset, user_id=x_user_id)
    if from_ts is not None:
        history = [item for item in history if item.feature_timestamp >= from_ts]
    if to_ts is not None:
        history = [item for item in history if item.feature_timestamp <= to_ts]
    return history
