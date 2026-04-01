import httpx

from app.models.order import OrderRequest


class ExchangeClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def place(self, payload: OrderRequest) -> dict:
        response = httpx.post(
            f"{self._base_url}/exchange/orders",
            json={
                "user_id": payload.user_id,
                "exchange": payload.exchange,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": payload.quantity,
                "requested_notional": payload.requested_notional,
                "shadow_mode": payload.shadow_mode,
                "api_key": getattr(payload, "api_key", None),
                "api_secret": getattr(payload, "api_secret", None),
                "credential_label": getattr(payload, "credential_label", None),
                "sandbox": getattr(payload, "credential_sandbox", True),
                "correlation_id": payload.correlation_id,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
