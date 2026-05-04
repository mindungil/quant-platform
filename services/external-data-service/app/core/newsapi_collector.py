"""NewsAPI.org crypto news collector.

Free tier: 100 requests/day, 30-day lookback.
Requires NEWSAPI_KEY env var (free registration at newsapi.org).
Falls back silently if key not set.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("newsapi-collector")

_API_KEY = os.getenv("NEWSAPI_KEY", "")

QUERIES = {
    "BTC": "bitcoin OR btc",
    "ETH": "ethereum OR eth",
    "SOL": "solana",
}


async def collect_newsapi(client: httpx.AsyncClient) -> list[dict]:
    """Collect crypto news from NewsAPI.org."""
    if not _API_KEY:
        logger.debug("NEWSAPI_KEY not set, skipping")
        return []

    items = []
    for asset, query in QUERIES.items():
        try:
            resp = await client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": _API_KEY,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug("newsapi %s: status %d", asset, resp.status_code)
                continue

            for article in resp.json().get("articles", []):
                title = article.get("title", "")
                desc = (article.get("description", "") or "")[:300]
                src = article.get("source", {}).get("name", "newsapi")
                pub = article.get("publishedAt", "")
                url = article.get("url", "")
                src_id = hashlib.sha256(url.encode()).hexdigest()[:12] if url else title[:20]

                items.append({
                    "id": hashlib.sha256(f"newsapi:{src_id}:{asset}".encode()).hexdigest()[:16],
                    "asset": asset,
                    "timestamp": pub or datetime.now(timezone.utc).isoformat(),
                    "source": "newsapi",
                    "source_id": src_id,
                    "title": title[:200],
                    "body": desc if desc else None,
                    "community_score": None,
                    "metadata": {"publisher": src, "url": url[:200]},
                })
        except Exception as e:
            logger.warning("newsapi %s: %s", asset, str(e)[:100])

    logger.info("newsapi: collected %d items", len(items))
    return items
