from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.core.indicators import calculate_features, interpolate_gaps
from app.db.repository import candle_repository, feature_repository
from app.models.feature import CandlePayload, FeatureResponse
from app.services.event_publisher import publisher
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return health_payload(
        "feature-store",
        {
            "timescaledb": check_sql("timescaledb", settings.timescale_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/events/candles/{asset}", response_model=FeatureResponse)
def ingest_candle(asset: str, payload: CandlePayload) -> FeatureResponse:
    candle_repository.add(asset, payload)
    candles = candle_repository.get(asset)
    candles = interpolate_gaps(candles)
    feature = calculate_features(asset=asset, candles=candles)
    feature_repository.save(asset, feature)
    publisher.publish_feature(asset=asset, feature=feature)
    return feature


@router.get("/features/{asset}/latest", response_model=FeatureResponse)
def get_latest_features(asset: str) -> FeatureResponse:
    feature = feature_repository.get_latest(asset)
    if feature is None:
        raise HTTPException(status_code=404, detail="features_not_found")
    return feature


@router.get("/features/{asset}/history", response_model=list[FeatureResponse])
def get_feature_history(asset: str, from_ts: datetime | None = None, to_ts: datetime | None = None) -> list[FeatureResponse]:
    history = feature_repository.get_history(asset)
    if from_ts is not None:
        history = [item for item in history if item.timestamp >= from_ts]
    if to_ts is not None:
        history = [item for item in history if item.timestamp <= to_ts]
    return history
