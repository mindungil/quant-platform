import httpx

from app.core.config import settings
from app.models.order import OrderRequest
from shared.internal_admin import build_internal_admin_headers
from shared.request_context import current_request_headers


class ExchangeClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def _headers(self, actor: str, path: str) -> dict[str, str]:
        return {
            **current_request_headers(),
            **build_internal_admin_headers(
                settings.internal_admin_secret,
                actor,
                path,
            ),
        }

    def cancel(self, order_id: str, user_id: str, exchange: str) -> dict:
        response = httpx.delete(
            f"{self._base_url}/exchange/orders/{order_id}",
            headers=self._headers(user_id, f"/exchange/orders/{order_id}"),
            params={"user_id": user_id, "exchange": exchange},
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()

    def place(self, payload: OrderRequest) -> dict:
        response = httpx.post(
            f"{self._base_url}/exchange/orders",
            headers=self._headers(payload.user_id, "/exchange/orders"),
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

    def get_status(self, order_id: str) -> dict | None:
        response = httpx.get(
            f"{self._base_url}/exchange/orders/{order_id}/status",
            headers=self._headers("order-service", f"/exchange/orders/{order_id}/status"),
            timeout=5.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_fills(self, order_id: str) -> list[dict]:
        response = httpx.get(
            f"{self._base_url}/exchange/orders/{order_id}/fills",
            headers=self._headers("order-service", f"/exchange/orders/{order_id}/fills"),
            timeout=5.0,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json()
