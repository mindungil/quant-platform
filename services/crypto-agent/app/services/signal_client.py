import httpx

from app.models.agent import SignalSnapshot
from shared.request_context import current_request_headers


class SignalClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_latest_signal(self, asset: str, *, user_id: str | None = None) -> SignalSnapshot:
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}
        response = httpx.get(f"{self._base_url}/signals/{asset}/latest", headers=headers, timeout=5.0)
        response.raise_for_status()
        return SignalSnapshot.model_validate(response.json())
