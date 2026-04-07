from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.models.decision import RiskApproval

logger = logging.getLogger(__name__)


async def check_risk(
    asset: str,
    requested_notional: float | None = None,
    max_notional: float | None = None,
    current_drawdown: float | None = None,
) -> RiskApproval:
    """Call risk-service pre-flight check."""
    url = f"{settings.risk_service_base_url.rstrip('/')}/risk/approve"
    payload = {
        "asset": asset,
        "requested_notional": requested_notional or settings.default_notional,
        "max_notional": max_notional or settings.max_notional,
        "current_drawdown": current_drawdown if current_drawdown is not None else settings.current_drawdown,
    }
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return RiskApproval.model_validate(response.json())
