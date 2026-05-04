import httpx

from app.core.config import settings
from app.models.order import OrderRequest
from shared.internal_admin import build_internal_admin_headers
from shared.request_context import current_request_headers


def _compute_realized_pnl(
    payload: OrderRequest,
    pre_fill_portfolio: dict | None,
    *,
    fill_quantity: float,
    fill_price: float,
) -> float:
    if not pre_fill_portfolio:
        return 0.0

    positions = pre_fill_portfolio.get("positions") or {}
    average_entry_prices = pre_fill_portfolio.get("average_entry_prices") or {}

    current_qty = float(positions.get(payload.asset, 0.0) or 0.0)
    avg_entry = float(average_entry_prices.get(payload.asset, 0.0) or 0.0)
    fill_price = float(fill_price or 0.0)
    fill_qty = abs(float(fill_quantity or 0.0))

    if fill_price <= 0 or fill_qty <= 0 or avg_entry <= 0:
        return 0.0

    if payload.side == "SELL" and current_qty > 0:
        closed_qty = min(current_qty, fill_qty)
        return round((fill_price - avg_entry) * closed_qty, 4)

    if payload.side == "BUY" and current_qty < 0:
        closed_qty = min(abs(current_qty), fill_qty)
        return round((avg_entry - fill_price) * closed_qty, 4)

    return 0.0


class StatisticsClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def record_trade(
        self,
        payload: OrderRequest,
        *,
        order_status: str,
        order_id: str | None = None,
        pre_fill_portfolio: dict | None = None,
        fill_quantity: float | None = None,
        fill_price: float | None = None,
    ) -> dict:
        pnl = 0.0
        effective_fill_quantity = float(fill_quantity if fill_quantity is not None else payload.quantity)
        effective_fill_price = float(fill_price if fill_price is not None else payload.price)
        if not order_status.startswith("REJECTED") and effective_fill_quantity > 0 and effective_fill_price > 0:
            pnl = _compute_realized_pnl(
                payload,
                pre_fill_portfolio,
                fill_quantity=effective_fill_quantity,
                fill_price=effective_fill_price,
            )
        response = httpx.post(
            f"{self._base_url}/statistics/record",
            headers={
                **current_request_headers(),
                **build_internal_admin_headers(
                    settings.internal_admin_secret,
                    payload.user_id,
                    "/statistics/record",
                ),
            },
            json={
                "user_id": payload.user_id,
                "strategy_id": payload.strategy_id,
                "agent_name": payload.agent_name,
                "lane": payload.lane,
                "order_id": order_id,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": effective_fill_quantity,
                "fill_price": effective_fill_price,
                "correlation_id": payload.correlation_id,
                "trade_pnls": [pnl],
                "expected_return": 0.02,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return response.json()
