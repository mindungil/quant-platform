import httpx

from app.models.signal import ExternalContextSnapshot
from shared.request_context import current_request_headers


class ExternalDataClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_external_context(self, asset: str) -> ExternalContextSnapshot:
        response = httpx.get(
            f"{self._base_url}/external/context/{asset}",
            headers=current_request_headers(),
            timeout=5.0,
        )
        response.raise_for_status()
        return ExternalContextSnapshot.model_validate(response.json())
