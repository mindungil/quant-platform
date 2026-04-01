import httpx

from app.models.order import OrderRequest


class RiskClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def approve(self, payload: OrderRequest) -> dict:
        response = httpx.post(
            f"{self._base_url}/risk/approve",
            json={
                "user_id": payload.user_id,
                "asset": payload.asset,
                "requested_notional": payload.requested_notional,
                "max_notional": payload.max_notional,
                "current_drawdown": payload.current_drawdown,
                "current_exposure": payload.current_exposure,
                "exposure_limit": payload.exposure_limit,
                "automation_enabled": payload.automation_enabled,
                "correlation_id": payload.correlation_id,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
