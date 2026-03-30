import httpx

from app.models.order import OrderRequest


class PortfolioClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def apply_fill(self, payload: OrderRequest, *, order_id: str, status: str) -> dict:
        response = httpx.post(
            f"{self._base_url}/portfolio/fills",
            json={
                "user_id": payload.user_id,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": payload.quantity,
                "price": payload.price,
                "notional": payload.requested_notional,
                "order_id": order_id,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
