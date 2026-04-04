from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.core.validator import detect_gaps, validate_candle_transition
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


VALID_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
INTERVAL_HOURS = {"4h": 4, "1d": 24}
SUB_HOUR_INTERVALS = {"1m", "5m", "15m"}


def _resample_candles(candles: list[CandlePayload], target_interval: str) -> list[CandlePayload]:
    """Resample 1h candles to a larger timeframe using OHLCV aggregation."""
    hours = INTERVAL_HOURS.get(target_interval)
    if hours is None or hours < 2:
        return candles

    result: list[CandlePayload] = []
    for i in range(0, len(candles), hours):
        group = candles[i : i + hours]
        if not group:
            break
        result.append(
            CandlePayload(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return result


@router.get("/candles/{asset}/history", response_model=list[CandlePayload])
def get_candle_history(asset: str, limit: int = 500, interval: str = "1h") -> list[CandlePayload]:
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"invalid_interval: must be one of {sorted(VALID_INTERVALS)}")

    if interval in SUB_HOUR_INTERVALS:
        raise HTTPException(
            status_code=422,
            detail="insufficient_resolution: only 1h base candles available, cannot produce sub-hour intervals",
        )

    # Fetch more candles if resampling to larger timeframe
    fetch_limit = limit
    if interval in INTERVAL_HOURS:
        fetch_limit = limit * INTERVAL_HOURS[interval]

    candles = market_data_repository.get_history(asset, limit=fetch_limit)
    if not candles:
        raise HTTPException(status_code=404, detail="no_candles_found")

    if interval != "1h":
        candles = _resample_candles(candles, interval)
        candles = candles[-limit:]  # trim to requested limit

    return candles


@router.get("/candles/{asset}/gaps")
def get_candle_gaps(asset: str, interval_minutes: int = 60) -> dict:
    candles = market_data_repository.get_history(asset)
    gaps = detect_gaps(candles, expected_interval_minutes=interval_minutes)
    return {"asset": asset, "interval_minutes": interval_minutes, "gaps": gaps, "gap_count": len(gaps)}


@router.get("/collectors")
def collectors():
    return list_collectors()
