import httpx

from app.models.agent import StrategySnapshot


class StrategyClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_active_strategy(self, asset_type: str) -> StrategySnapshot:
        response = httpx.get(
            f"{self._base_url}/strategies/active",
            params={"asset_type": asset_type},
            timeout=5.0,
        )
        response.raise_for_status()
        return StrategySnapshot.model_validate(response.json())
