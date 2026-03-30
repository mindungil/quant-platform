from fastapi import APIRouter, HTTPException

from app.core.validator import validate_candle_transition
from app.db.repository import market_data_repository
from app.models.candle import CandleIngestResponse, CandlePayload, CandleUpdatedEvent
from app.services.collectors import list_collectors
from app.services.event_publisher import publisher

router = APIRouter()
_last_candles: dict[str, CandlePayload] = {}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@router.get("/collectors")
def collectors():
    return list_collectors()
