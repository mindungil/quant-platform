import httpx


class StrategyRegistryClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_active_strategy(self, asset_type: str) -> dict | None:
        response = httpx.get(
            f"{self._base_url}/strategies/active",
            params={"asset_type": asset_type},
            timeout=5.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
