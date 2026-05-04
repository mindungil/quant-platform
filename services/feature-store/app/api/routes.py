from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from app.core.config import settings
from app.core.indicators import calculate_features, interpolate_gaps
from app.db.repository import candle_repository, feature_repository
from app.models.feature import CandlePayload, FeatureResponse
from app.services.event_publisher import publisher
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()

# Phase D: feature integrity metrics. Freshness gauge is consumed by
# alerts (fire when any asset's lag > 10 minutes on 1h bars); PIT hit /
# miss counters track look-ahead safety in downstream backtests.
feature_age_seconds = Gauge(
    "feature_age_seconds",
    "Seconds since the most recent feature snapshot was persisted, per asset",
    ["asset"],
)
feature_pit_requests_total = Counter(
    "feature_pit_requests_total",
    "Point-in-time feature reads (used by backtests to enforce no-look-ahead)",
    ["asset", "result"],
)


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
    # Expose freshness so Prometheus alerts fire before stale features
    # start corrupting live signals.
    try:
        age = (datetime.now(timezone.utc) - feature.timestamp).total_seconds()
        feature_age_seconds.labels(asset=asset).set(max(age, 0.0))
    except Exception:
        pass
    return feature


@router.get("/features/{asset}/history", response_model=list[FeatureResponse])
def get_feature_history(asset: str, from_ts: datetime | None = None, to_ts: datetime | None = None) -> list[FeatureResponse]:
    history = feature_repository.get_history(asset)
    if from_ts is not None:
        history = [item for item in history if item.timestamp >= from_ts]
    if to_ts is not None:
        history = [item for item in history if item.timestamp <= to_ts]
    return history


@router.get("/features/{asset}/as-of", response_model=FeatureResponse)
def get_features_as_of(asset: str, ts: datetime) -> FeatureResponse:
    """Point-in-time feature read: return the newest snapshot ≤ *ts*.

    Backtests call this to avoid look-ahead — even if feature-store has
    newer data buffered, a simulation rerun for time T must only see
    what was computable at T. Live trading should keep using
    `/features/{asset}/latest`.
    """
    history = feature_repository.get_history(asset)
    eligible = [item for item in history if item.timestamp <= ts]
    if not eligible:
        feature_pit_requests_total.labels(asset=asset, result="miss").inc()
        raise HTTPException(status_code=404, detail="no_features_before_ts")
    feature_pit_requests_total.labels(asset=asset, result="hit").inc()
    # get_history returns chronological order; newest ≤ ts is the last.
    return max(eligible, key=lambda item: item.timestamp)


@router.get("/features/freshness")
def features_freshness():
    """Freshness snapshot across all tracked assets.

    Returns {asset: age_seconds} so operators + alert rules can treat
    stale features as a first-class data issue rather than discovering
    them via signal degradation.
    """
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for asset in candle_repository.list_assets():
        latest = feature_repository.get_latest(asset)
        if latest is None:
            out[asset] = {"age_seconds": None, "status": "missing"}
            continue
        age = (now - latest.timestamp).total_seconds()
        feature_age_seconds.labels(asset=asset).set(max(age, 0.0))
        out[asset] = {
            "age_seconds": round(age, 1),
            "status": "stale" if age > 600 else "fresh",
            "timestamp": latest.timestamp.isoformat(),
        }
    return out
