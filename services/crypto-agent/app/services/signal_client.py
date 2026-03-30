import httpx

from app.models.agent import SignalSnapshot


class SignalClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_latest_signal(self, asset: str) -> SignalSnapshot:
        response = httpx.get(f"{self._base_url}/signals/{asset}/latest", timeout=5.0)
        response.raise_for_status()
        return SignalSnapshot.model_validate(response.json())
