"""Multi-source sentiment collector.

Collects crypto sentiment from free APIs:
  - CryptoPanic: news + community votes (requires CRYPTOPANIC_TOKEN)
  - RSS feeds: CoinDesk, CoinTelegraph, Bitcoin Magazine, TheDefiant, Decrypt, TheBlock
  - Alternative.me: Fear & Greed index
  - Reddit: social posts (optional, rate-limited)
  - NewsAPI: news articles (requires NEWSAPI_KEY)

Each collector returns a list of SentimentItem dicts ready for DB insertion.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger("sentiment-collector")

ASSETS = ["BTC", "ETH", "SOL"]

_CRYPTOPANIC_TOKEN = os.getenv("CRYPTOPANIC_TOKEN", "")

# RSS feeds with polling tier: fast (3min), slow (15min), rare (30min)
# Based on actual CDN cache headers and article publish frequency
_RSS_FEEDS_FAST = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),      # no cache, ~30min articles
    ("decrypt", "https://decrypt.co/feed"),                                # 10s cache, ~20min articles
    ("theblock", "https://www.theblock.co/rss.xml"),                       # 60s cache, ~1h articles
]
_RSS_FEEDS_SLOW = [
    ("cointelegraph", "https://cointelegraph.com/rss"),                    # 4h CDN cache
    ("thedefiant", "https://thedefiant.io/feed"),                          # ~2h article interval
    # bitcoinmagazine removed: returns 403 Forbidden consistently
]

# Combined for backward compat
_RSS_FEEDS = _RSS_FEEDS_FAST + _RSS_FEEDS_SLOW

# Skip patterns: site name titles that aren't real articles
_RSS_SKIP_TITLES = {
    "coindesk", "cointelegraph", "bitcoin magazine", "the defiant",
    "decrypt", "the block", "cointelegraph.com news",
    "coindesk: bitcoin, ethereum, crypto news and price data",
    "the block | bitcoin, ethereum, and crypto news",
    "bitcoin magazine – bitcoin news, articles and expert insights",
    "the defiant – defi news",
}

# Keyword scorer (fallback when NLP unavailable)
_BULL = [
    "rally", "surge", "bull", "breakout", "adoption", "partnership",
    "approval", "etf", "institutional", "record", "all-time high",
    "accumulation", "upgrade", "buy", "long", "positive", "bullish",
]
_BEAR = [
    "crash", "dump", "bear", "hack", "ban", "fraud", "lawsuit",
    "regulation", "sell-off", "liquidation", "fud", "fear",
    "collapse", "scam", "sell", "short", "negative", "bearish",
]


def _keyword_score(text: str) -> float:
    lower = text.lower()
    bull = sum(1 for k in _BULL if k in lower)
    bear = sum(1 for k in _BEAR if k in lower)
    total = bull + bear
    return (bull - bear) / total if total else 0.0


def _item_id(source: str, source_id: str) -> str:
    return hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()[:16]


_ASSET_ALIASES = {
    "BTC": ["BTC", "BITCOIN", "₿", "SATS", "SATOSHI"],
    "ETH": ["ETH", "ETHEREUM", "ETHER"],
    "SOL": ["SOL", "SOLANA"],
}


def _detect_asset(text: str) -> list[str]:
    """Detect which assets are mentioned in text."""
    t = text.upper()
    found = set()
    for asset, aliases in _ASSET_ALIASES.items():
        for alias in aliases:
            if alias in t:
                found.add(asset)
                break
    return list(found) or ["BTC"]  # default to BTC if no asset detected


# ─── CryptoPanic ──────────────────────────────────────────────

async def collect_cryptopanic(client: httpx.AsyncClient) -> list[dict]:
    """CryptoPanic API: news with community votes. Requires CRYPTOPANIC_TOKEN."""
    if not _CRYPTOPANIC_TOKEN:
        logger.debug("CRYPTOPANIC_TOKEN not set, skipping")
        return []

    items = []
    for asset in ASSETS:
        try:
            resp = await client.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={
                    "auth_token": _CRYPTOPANIC_TOKEN,
                    "currencies": asset,
                    "public": "true",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug("cryptopanic %s: status %d", asset, resp.status_code)
                continue
            for post in resp.json().get("results", [])[:20]:
                title = post.get("title", "")
                votes = post.get("votes", {})
                pos = votes.get("positive", 0)
                neg = votes.get("negative", 0)
                community = (pos - neg) / (pos + neg) if (pos + neg) > 0 else None
                src_id = str(post.get("id", title[:40]))

                items.append({
                    "id": _item_id("cryptopanic", src_id),
                    "asset": asset,
                    "timestamp": post.get("published_at", datetime.now(timezone.utc).isoformat()),
                    "source": "cryptopanic",
                    "source_id": src_id,
                    "title": title,
                    "body": None,
                    "community_score": round(community, 4) if community is not None else None,
                    "metadata": {"votes_pos": pos, "votes_neg": neg, "domain": post.get("domain", "")},
                })
        except Exception as e:
            logger.warning("cryptopanic_%s: %s", asset, str(e)[:100])
    return items


# ─── RSS Feeds ────────────────────────────────────────────────

async def collect_rss(client: httpx.AsyncClient, feeds: list[tuple[str, str]] | None = None) -> list[dict]:
    """Collect headlines from crypto RSS feeds.

    Parses <item> blocks individually to avoid title/pubDate misalignment
    (channel-level <title> tags have no matching <pubDate>).
    """
    items = []
    for source_name, url in (feeds or _RSS_FEEDS):
        try:
            resp = await client.get(url, timeout=15)
            if resp.status_code != 200:
                logger.debug("rss %s: status %d", source_name, resp.status_code)
                continue

            # Parse per-<item> block to keep title/pubDate aligned
            item_blocks = re.findall(
                r"<item[^>]*>(.*?)</item>",
                resp.text,
                re.DOTALL,
            )

            collected = 0
            for block in item_blocks[:25]:
                # Extract title (handle CDATA)
                title_m = re.search(
                    r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                    block,
                    re.DOTALL,
                )
                if not title_m:
                    continue
                title = title_m.group(1).strip()
                if not title or title.lower() in _RSS_SKIP_TITLES:
                    continue

                # Extract pubDate from the same <item> block
                date_m = re.search(r"<pubDate>(.*?)</pubDate>", block)
                ts = date_m.group(1).strip() if date_m else None
                if not ts:
                    # No pubDate → skip (don't use NOW which causes duplicates)
                    continue

                # Extract body/description preview
                desc_m = re.search(
                    r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>",
                    block,
                    re.DOTALL,
                )
                body = desc_m.group(1).strip()[:500] if desc_m else None

                assets = _detect_asset(title)
                src_id = hashlib.sha256(title.encode()).hexdigest()[:12]

                for asset in assets:
                    items.append({
                        "id": _item_id(source_name, f"{asset}_{src_id}"),
                        "asset": asset,
                        "timestamp": ts,
                        "source": source_name,
                        "source_id": src_id,
                        "title": title,
                        "body": body,
                        "community_score": None,
                        "metadata": {},
                    })
                    collected += 1
            logger.debug("rss %s: %d items", source_name, collected)
        except Exception as e:
            logger.warning("rss_%s: %s", source_name, str(e)[:100])
    return items


# ─── Fear & Greed ─────────────────────────────────────────────

async def collect_fng(client: httpx.AsyncClient) -> dict | None:
    """Alternative.me Fear & Greed — returns latest value, not items."""
    try:
        resp = await client.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [{}])[0]
            return {
                "value": int(data.get("value", 50)),
                "classification": data.get("value_classification", ""),
                "timestamp": datetime.fromtimestamp(
                    int(data.get("timestamp", 0)), tz=timezone.utc
                ).isoformat(),
            }
    except Exception as e:
        logger.warning("fng: %s", str(e)[:100])
    return None


# ─── Orchestrator ─────────────────────────────────────────────

async def collect_all(
    tier: str = "all",
) -> dict[str, Any]:
    """Run collectors based on polling tier.

    Args:
        tier: "fast" (3min), "slow" (15min), "rare" (30min), or "all"

    Tier schedule (managed by daemon):
      fast  — CoinDesk, Decrypt, TheBlock (every 3 min)
      slow  — CoinTelegraph, TheDefiant, BitcoinMagazine (every 15 min)
      rare  — NewsAPI, CryptoPanic, FNG, Reddit (every 30 min)
      all   — everything (backward compat)
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; quant-research/2.0)"},
        follow_redirects=True,
    ) as client:
        items: list[dict] = []
        fng = None

        if tier in ("fast", "all"):
            items.extend(await collect_rss(client, _RSS_FEEDS_FAST))

        if tier in ("slow", "all"):
            items.extend(await collect_rss(client, _RSS_FEEDS_SLOW))

        if tier in ("rare", "all"):
            fng = await collect_fng(client)
            items.extend(await collect_cryptopanic(client))
            try:
                from app.core.reddit_collector import collect_reddit
                items.extend(await collect_reddit(client))
            except Exception as e:
                logger.debug("reddit skipped: %s", str(e)[:80])
            try:
                from app.core.newsapi_collector import collect_newsapi
                items.extend(await collect_newsapi(client))
            except Exception as e:
                logger.debug("newsapi skipped: %s", str(e)[:80])

        # Keyword score fallback
        for item in items:
            if item.get("community_score") is None:
                item["community_score"] = round(_keyword_score(item["title"]), 4)

        sources = set(i["source"] for i in items)
        if items:
            logger.info("[%s] %d items from %s",
                        tier, len(items), ", ".join(sorted(sources)))

        return {"items": items, "fng": fng}
