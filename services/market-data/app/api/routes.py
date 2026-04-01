from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.core.validator import validate_candle_transition
from app.db.repository import market_data_repository
from app.models.candle import CandleIngestResponse, CandlePayload, CandleUpdatedEvent
from app.services.collectors import list_collectors
from app.services.event_publisher import publisher
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()
_last_candles: dict[str, CandlePayload] = {}


@router.get("/health")
def health() -> dict:
    return health_payload(
        "market-data",
        {
            "timescaledb": check_sql("timescaledb", settings.timescale_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/candles/{asset}", response_model=CandleIngestResponse)
def ingest_candle(asset: str, payload: CandlePayload) -> CandleIngestResponse:
    previous = _last_candles.get(asset)
    validation = validate_candle_transition(previous, payload)
    if not validation.accepted:
        raise HTTPException(status_code=422, detail=validation.reason)

    _last_candles[asset] = payload
    market_data_repository.save(asset, payload, anomaly_detected=validation.anomaly_detected)
    publisher.publish_market_candle(
        asset=asset,
        event=CandleUpdatedEvent(
            asset=asset,
            subject=f"market.candle.updated.{asset}",
            anomaly_detected=validation.anomaly_detected,
            candle=payload,
        ),
    )

    return CandleIngestResponse(
        asset=asset,
        accepted=True,
        anomaly_detected=validation.anomaly_detected,
        event_subject=f"market.candle.updated.{asset}",
    )


@router.get("/candles/{asset}/latest", response_model=CandlePayload)
def get_latest_candle(asset: str) -> CandlePayload:
    candle = market_data_repository.get_latest(asset)
    if candle is None:
        raise HTTPException(status_code=404, detail="candle_not_found")
    return candle


@router.get("/collectors")
def collectors():
    return list_collectors()
