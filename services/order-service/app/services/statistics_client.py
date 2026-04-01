import httpx

from app.models.order import OrderRequest
from shared.request_context import current_request_headers


class StatisticsClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def record_trade(self, payload: OrderRequest, *, order_status: str, order_id: str | None = None) -> dict:
        signed = payload.requested_notional if payload.side == "SELL" else -payload.requested_notional
        pnl = 0.0 if order_status.startswith("REJECTED") else round(signed * 0.01, 4)
        response = httpx.post(
            f"{self._base_url}/statistics/record",
            headers=current_request_headers(),
            json={
                "user_id": payload.user_id,
                "order_id": order_id,
                "asset": payload.asset,
                "correlation_id": payload.correlation_id,
                "trade_pnls": [pnl],
                "expected_return": 0.02,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
