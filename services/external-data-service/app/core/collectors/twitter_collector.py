"""Twitter/X sentiment collector via public search API proxies.

Uses Nitter RSS mirrors and social sentiment aggregators to collect
crypto-related tweets without requiring Twitter API keys.

Sources:
  - Nitter RSS mirrors (public)
  - LunarCrush social metrics (if API key available)
  - CryptoCompare social stats (free tier)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("twitter-collector")

# Nitter mirrors for RSS feeds (no auth needed)
NITTER_MIRRORS = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

# Key crypto accounts to track
CRYPTO_ACCOUNTS = [
    "caboruscz",     # CZ (Binance)
    "VitalikButerin",
    "saborhamlin",   # Justin Sun
    "brian_armstrong",
    "elikimanganyi",
    "whale_alert",
    "BitcoinMagazine",
    "WatcherGuru",
    "CryptoQuantNews",
]

LUNARCRUSH_API_KEY = os.getenv("LUNARCRUSH_API_KEY", "")
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")

# Asset keywords for detection
ASSET_KEYWORDS = {
    "BTC": ["bitcoin", "btc", "$btc"],
    "ETH": ["ethereum", "eth", "$eth", "ether"],
    "SOL": ["solana", "sol", "$sol"],
    "BNB": ["binance", "bnb", "$bnb"],
}

# Sentiment keywords
BULL_KEYWORDS = {"bullish", "pump", "moon", "breakout", "rally", "surge", "ath", "buy", "long", "parabolic"}
BEAR_KEYWORDS = {"bearish", "dump", "crash", "plunge", "sell", "short", "liquidat", "collapse", "rug"}


@dataclass
class SocialItem:
    """A single social media sentiment item."""
    id: str
    source: str
    title: str
    body: str | None
    asset: str
    timestamp: datetime
    author: str
    sentiment_score: float  # [-1, 1]
    confidence: float       # [0, 1]
    engagement: int         # likes + retweets
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title[:200],
            "body": self.body[:500] if self.body else None,
            "asset": self.asset,
            "timestamp": self.timestamp.isoformat(),
            "author": self.author,
            "nlp_score": self.sentiment_score,
            "nlp_confidence": self.confidence,
            "severity": max(1.0, abs(self.sentiment_score) * 2),
            "engagement": self.engagement,
            "url": self.url,
        }


def _detect_asset(text: str) -> str:
    """Detect which crypto asset a text is about."""
    text_lower = text.lower()
    for asset, keywords in ASSET_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return asset
    return "BTC"  # default


def _keyword_sentiment(text: str) -> tuple[float, float]:
    """Quick keyword-based sentiment scoring."""
    text_lower = text.lower()
    bull = sum(1 for kw in BULL_KEYWORDS if kw in text_lower)
    bear = sum(1 for kw in BEAR_KEYWORDS if kw in text_lower)
    total = bull + bear
    if total == 0:
        return 0.0, 0.3
    score = (bull - bear) / total
    confidence = min(0.4 + total * 0.1, 0.8)
    return score, confidence


def _item_id(source: str, title: str, author: str) -> str:
    return hashlib.sha256(f"{source}:{author}:{title[:100]}".encode()).hexdigest()[:16]


async def collect_nitter_rss(
    accounts: list[str] | None = None,
    timeout: float = 10.0,
) -> list[SocialItem]:
    """Collect tweets from Nitter RSS mirrors."""
    accounts = accounts or CRYPTO_ACCOUNTS
    items: list[SocialItem] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for mirror in NITTER_MIRRORS:
            if items:  # got results from first mirror
                break
            for account in accounts:
                try:
                    url = f"{mirror}/{account}/rss"
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    root = ET.fromstring(resp.text)
                    for item_el in root.findall(".//item")[:10]:
                        title_el = item_el.find("title")
                        desc_el = item_el.find("description")
                        pub_el = item_el.find("pubDate")
                        link_el = item_el.find("link")

                        if title_el is None or title_el.text is None:
                            continue

                        title = title_el.text.strip()
                        body = desc_el.text.strip() if desc_el is not None and desc_el.text else None
                        # Strip HTML tags from body
                        if body:
                            body = re.sub(r"<[^>]+>", " ", body).strip()

                        # Parse date
                        ts = datetime.now(timezone.utc)
                        if pub_el is not None and pub_el.text:
                            try:
                                from email.utils import parsedate_to_datetime
                                ts = parsedate_to_datetime(pub_el.text)
                                if ts.tzinfo is None:
                                    ts = ts.replace(tzinfo=timezone.utc)
                            except Exception:
                                pass

                        asset = _detect_asset(title + " " + (body or ""))
                        score, conf = _keyword_sentiment(title + " " + (body or ""))

                        items.append(SocialItem(
                            id=_item_id("twitter", title, account),
                            source="twitter",
                            title=title,
                            body=body,
                            asset=asset,
                            timestamp=ts,
                            author=account,
                            sentiment_score=score,
                            confidence=conf,
                            engagement=0,
                            url=link_el.text if link_el is not None else None,
                        ))
                except Exception as exc:
                    logger.debug("nitter_rss_failed", extra={
                        "mirror": mirror, "account": account,
                        "error": str(exc)[:100],
                    })
                    continue

    logger.info("twitter_collected", extra={"count": len(items)})
    return items


async def collect_lunarcrush(
    assets: list[str] | None = None,
    timeout: float = 15.0,
) -> list[SocialItem]:
    """Collect social metrics from LunarCrush API."""
    if not LUNARCRUSH_API_KEY:
        return []

    assets = assets or ["BTC", "ETH", "SOL"]
    items: list[SocialItem] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        for asset in assets:
            try:
                resp = await client.get(
                    "https://lunarcrush.com/api4/public/coins/list/v2",
                    params={"symbol": asset},
                    headers={"Authorization": f"Bearer {LUNARCRUSH_API_KEY}"},
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                coin_data = data.get("data", [])
                if not coin_data:
                    continue

                coin = coin_data[0] if isinstance(coin_data, list) else coin_data
                sentiment = float(coin.get("sentiment", 50)) / 100 - 0.5  # normalize to [-0.5, 0.5]
                galaxy_score = float(coin.get("galaxy_score", 50))
                social_volume = int(coin.get("social_volume", 0))

                items.append(SocialItem(
                    id=_item_id("lunarcrush", f"{asset}_social_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}", "lunarcrush"),
                    source="lunarcrush",
                    title=f"{asset} social sentiment: {sentiment:+.2f}, galaxy score: {galaxy_score:.0f}",
                    body=f"Social volume: {social_volume}, Galaxy: {galaxy_score}",
                    asset=asset,
                    timestamp=datetime.now(timezone.utc),
                    author="lunarcrush",
                    sentiment_score=sentiment * 2,  # scale to [-1, 1]
                    confidence=0.6,
                    engagement=social_volume,
                ))
            except Exception as exc:
                logger.debug("lunarcrush_failed", extra={"asset": asset, "error": str(exc)[:100]})

    return items


async def collect_social_sentiment() -> list[SocialItem]:
    """Collect from all available social sources."""
    results = await asyncio.gather(
        collect_nitter_rss(),
        collect_lunarcrush(),
        return_exceptions=True,
    )

    items: list[SocialItem] = []
    for r in results:
        if isinstance(r, list):
            items.extend(r)
        elif isinstance(r, Exception):
            logger.warning("social_collector_error", extra={"error": str(r)[:100]})

    return items
