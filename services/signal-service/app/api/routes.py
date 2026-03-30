from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.scoring import build_signal_response
from app.db.repository import signal_repository
from app.services.event_publisher import publisher
from app.services.external_data_client import ExternalDataClient
from app.services.feature_store_client import FeatureStoreClient

router = APIRouter()
client = FeatureStoreClient(base_url=settings.feature_store_base_url)
external_client = ExternalDataClient(base_url=settings.external_data_service_base_url)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/signals/evaluate/{asset}")
def evaluate_signal(asset: str):
    features = client.get_latest_features(asset)
    external_context = external_client.get_external_context(asset)
    evaluation = build_signal_response(
        asset=asset,
        features=features,
        threshold=settings.signal_threshold,
        external_context=external_context,
        external_signal_weight=settings.external_signal_weight,
    )
    signal_repository.save(asset=asset, evaluation=evaluation)
    if evaluation.threshold_crossed:
        publisher.publish_threshold(asset=asset, asset_type="crypto", evaluation=evaluation)
    return evaluation


@router.get("/signals/{asset}/latest")
def get_latest_signal(asset: str):
    evaluation = signal_repository.get_latest(asset)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    return evaluation


@router.get("/signals")
def list_latest_signals():
    return signal_repository.list_latest()
