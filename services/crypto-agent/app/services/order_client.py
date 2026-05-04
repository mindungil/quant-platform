from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.models.decision import OrderRequest, OrderResult
from shared.internal_admin import build_internal_admin_headers

logger = logging.getLogger(__name__)


async def submit_order(
    asset: str,
    side: str,
    shadow_mode: bool = False,
    *,
    user_id: str | None = None,
    lane: str = "agent_core",
    lane_budget_pct: float = 1.0,
    subscription_id: str | None = None,
    template_id: str | None = None,
    strategy_id: str | None = None,
    agent_name: str = "crypto-agent",
) -> OrderResult:
    """Submit an order via order-service.

    In dual-lane mode, notional is scaled by lane_budget_pct. The lane tag
    and subscription_id are forwarded for bookkeeping (order-service accepts
    them as optional fields).
    """
    url = f"{settings.order_service_base_url.rstrip('/')}/orders"
    scaled_notional = settings.default_notional * max(0.0, min(1.0, lane_budget_pct))
    effective_user_id = user_id or settings.default_user_id
    payload = OrderRequest(
        user_id=effective_user_id,
        exchange=settings.default_exchange,
        asset=asset,
        side=side,
        quantity=settings.default_quantity * max(0.0, min(1.0, lane_budget_pct)),
        requested_notional=scaled_notional,
        max_notional=settings.max_notional,
        current_drawdown=settings.current_drawdown,
        shadow_mode=shadow_mode or settings.shadow_mode,
        strategy_id=strategy_id,
        agent_name=agent_name,
        lane=lane,
        lane_budget_pct=max(0.0, min(1.0, lane_budget_pct)),
        subscription_id=subscription_id,
        template_id=template_id,
    )
    body = payload.model_dump(mode="json")
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        response = await client.post(
            url,
            json=body,
            headers=build_internal_admin_headers(
                settings.internal_admin_secret,
                effective_user_id,
                "/orders",
            ),
        )
        response.raise_for_status()
        return OrderResult.model_validate(response.json())
