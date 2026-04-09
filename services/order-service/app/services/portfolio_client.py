import hmac
import time
from hashlib import sha256

import httpx

from app.core.config import settings
from app.models.order import OrderRequest
from shared.request_context import current_request_headers


def _build_internal_admin_headers(actor_user_id: str, path: str) -> dict[str, str]:
    ts = str(int(time.time()))
    message = f"{actor_user_id}:{ts}:{path}"
    sig = hmac.new(settings.internal_admin_secret.encode(), message.encode(), sha256).hexdigest()
    return {
        "X-Internal-Actor-User-ID": actor_user_id,
        "X-Internal-Admin-Timestamp": ts,
        "X-Internal-Admin-Signature": sig,
    }


class PortfolioClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def apply_fill(self, payload: OrderRequest, *, order_id: str, status: str) -> dict:
        headers = {
            **current_request_headers(),
            **_build_internal_admin_headers(payload.user_id, "/portfolio/fills"),
        }
        response = httpx.post(
            f"{self._base_url}/portfolio/fills",
            headers=headers,
            json={
                "user_id": payload.user_id,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": payload.quantity,
                "price": payload.price,
                "notional": payload.requested_notional,
                "order_id": order_id,
                "correlation_id": payload.correlation_id,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
