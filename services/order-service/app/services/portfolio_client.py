import httpx

from app.core.config import settings
from app.models.order import OrderRequest
from shared.internal_admin import build_internal_admin_headers
from shared.request_context import current_request_headers


class PortfolioClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_snapshot(self, user_id: str) -> dict | None:
        response = httpx.get(
            f"{self._base_url}/portfolio/{user_id}",
            headers=current_request_headers(),
            timeout=5.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def apply_fill(
        self,
        payload: OrderRequest,
        *,
        order_id: str,
        status: str,
        fill_quantity: float,
        fill_price: float,
        filled_notional: float | None = None,
    ) -> dict:
        headers = {
            **current_request_headers(),
            **build_internal_admin_headers(
                settings.internal_admin_secret,
                payload.user_id,
                "/portfolio/fills",
            ),
        }
        response = httpx.post(
            f"{self._base_url}/portfolio/fills",
            headers=headers,
            json={
                "user_id": payload.user_id,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": fill_quantity,
                "price": fill_price,
                "notional": filled_notional if filled_notional is not None else fill_quantity * fill_price,
                "order_id": order_id,
                "correlation_id": payload.correlation_id,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
