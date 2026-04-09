from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.models.decision import OrderRequest, OrderResult

logger = logging.getLogger(__name__)


async def submit_order(asset: str, side: str, shadow_mode: bool = False) -> OrderResult:
    """Submit an order via order-service."""
    url = f"{settings.order_service_base_url.rstrip('/')}/orders"
    payload = OrderRequest(
        user_id=settings.default_user_id,
        exchange=settings.default_exchange,
        asset=asset,
        side=side,
        quantity=settings.default_quantity,
        requested_notional=settings.default_notional,
        max_notional=settings.max_notional,
        current_drawdown=settings.current_drawdown,
        shadow_mode=shadow_mode or settings.shadow_mode,
    )
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        response = await client.post(url, json=payload.model_dump(mode="json"))
        response.raise_for_status()
        return OrderResult.model_validate(response.json())
