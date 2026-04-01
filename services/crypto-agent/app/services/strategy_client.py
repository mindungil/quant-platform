import httpx

from app.models.agent import StrategySnapshot
from shared.request_context import current_request_headers


class StrategyClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_active_strategy(self, asset_type: str, *, user_id: str | None = None) -> StrategySnapshot:
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}
        response = httpx.get(
            f"{self._base_url}/strategies/active",
            headers=headers,
            params={"asset_type": asset_type},
            timeout=5.0,
        )
        response.raise_for_status()
        return StrategySnapshot.model_validate(response.json())
