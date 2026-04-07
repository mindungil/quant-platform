from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def get_latest_features(asset: str) -> dict[str, Any]:
    """Fetch the latest feature vector for *asset* from feature-store."""
    url = f"{settings.feature_store_base_url.rstrip('/')}/features/{asset}/latest"
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and "features" in data:
            return data["features"]
        return data
