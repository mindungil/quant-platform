"""News Sentiment Pipeline — crypto news analysis without heavy ML models.

Uses CryptoPanic API (free tier) for pre-scored news + keyword-based fallback.
Produces a normalized sentiment score [-1, 1] for integration with the factor system.
"""
import logging
import time
import re
from typing import Optional

import httpx

logger = logging.getLogger("external-data")

# Simple sentiment keywords (no ML required)
BULLISH_KEYWORDS = [
    "rally", "surge", "bull", "breakout", "adoption", "partnership",
    "approval", "etf", "institutional", "record", "all-time high",
    "accumulation", "upgrade", "buy", "long", "positive",
    "\uc0c1\uc2b9", "\uae09\ub4f1", "\ub3cc\ud30c", "\ud638\uc7ac", "\ub9e4\uc218", "\uac15\uc138",
]
BEARISH_KEYWORDS = [
    "crash", "dump", "bear", "hack", "ban", "fraud", "lawsuit",
    "regulation", "sell-off", "liquidation", "fud", "fear",
    "collapse", "scam", "sell", "short", "negative",
    "\ud558\ub77d", "\uae09\ub77d", "\ud3ed\ub77d", "\uc545\uc7ac", "\ub9e4\ub3c4", "\uc57d\uc138",
]

_cache: dict = {"data": None, "ts": 0}
CACHE_TTL = 600  # 10 minutes


def _keyword_score(text: str) -> float:
    """Simple keyword-based sentiment: [-1, 1]."""
    text_lower = text.lower()
    bull = sum(1 for k in BULLISH_KEYWORDS if k in text_lower)
    bear = sum(1 for k in BEARISH_KEYWORDS if k in text_lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


async def fetch_crypto_news(asset: str = "BTC") -> list[dict]:
    """Fetch recent crypto news from CryptoPanic (free, no API key needed for basic)."""
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]

    news: list[dict] = []

    # Source 1: CryptoPanic public feed
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://cryptopanic.com/api/free/v1/posts/?currencies={asset}&public=true",
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", [])[:20]:
                    title = item.get("title", "")
                    # CryptoPanic provides votes
                    votes = item.get("votes", {})
                    positive = votes.get("positive", 0)
                    negative = votes.get("negative", 0)

                    if positive + negative > 0:
                        api_score = (positive - negative) / (positive + negative)
                    else:
                        api_score = _keyword_score(title)

                    news.append({
                        "title": title,
                        "source": item.get("source", {}).get("title", ""),
                        "score": round(api_score, 3),
                        "published": item.get("published_at", ""),
                    })
    except Exception as e:
        logger.debug("cryptopanic_fetch_failed: %s", e)

    # Source 2: Simple RSS keyword analysis (fallback)
    if not news:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # CoinDesk RSS
                resp = await client.get("https://www.coindesk.com/arc/outboundfeeds/rss/")
                if resp.status_code == 200:
                    titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", resp.text)
                    for title in titles[:10]:
                        news.append({
                            "title": title,
                            "source": "CoinDesk",
                            "score": round(_keyword_score(title), 3),
                        })
        except Exception as e:
            logger.debug("rss_fetch_failed: %s", e)

    _cache["data"] = news
    _cache["ts"] = now
    return news


async def compute_news_sentiment(asset: str = "BTC") -> dict:
    """Compute aggregate news sentiment score."""
    news = await fetch_crypto_news(asset)

    if not news:
        return {
            "score": 0.0,
            "article_count": 0,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "source": "none",
        }

    scores = [n["score"] for n in news]
    avg_score = sum(scores) / len(scores) if scores else 0

    positive = sum(1 for s in scores if s > 0.1)
    negative = sum(1 for s in scores if s < -0.1)
    neutral = len(scores) - positive - negative

    return {
        "score": round(avg_score, 4),
        "article_count": len(news),
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
        "top_headlines": [n["title"] for n in news[:5]],
        "source": "cryptopanic+keyword",
    }
