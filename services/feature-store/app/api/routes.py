from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.core.indicators import calculate_features
from app.db.repository import candle_repository, feature_repository
from app.models.feature import CandlePayload, FeatureResponse
from app.services.event_publisher import publisher

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/events/candles/{asset}", response_model=FeatureResponse)
def ingest_candle(asset: str, payload: CandlePayload) -> FeatureResponse:
    candle_repository.add(asset, payload)
    candles = candle_repository.get(asset)
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
