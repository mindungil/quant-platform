import httpx

from app.models.signal import FeatureSnapshot
from shared.request_context import current_request_headers


class FeatureStoreClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_latest_features(self, asset: str) -> FeatureSnapshot:
        response = httpx.get(f"{self._base_url}/features/{asset}/latest", headers=current_request_headers(), timeout=5.0)
        response.raise_for_status()
        return FeatureSnapshot.model_validate(response.json())
