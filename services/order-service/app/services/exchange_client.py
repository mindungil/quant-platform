import httpx

from app.models.order import OrderRequest


class ExchangeClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def place(self, payload: OrderRequest) -> dict:
        response = httpx.post(
            f"{self._base_url}/exchange/orders",
            json={
                "exchange": payload.exchange,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": payload.quantity,
                "shadow_mode": payload.shadow_mode,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
