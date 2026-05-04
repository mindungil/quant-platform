"""Reddit crypto sentiment collector.

Uses Reddit's public JSON API (no auth needed for read-only).
Collects from r/bitcoin, r/ethereum, r/solana, r/cryptocurrency.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("reddit-collector")

SUBREDDITS = {
    "BTC": ["bitcoin", "cryptocurrency"],
    "ETH": ["ethereum", "cryptocurrency"],
    "SOL": ["solana", "cryptocurrency"],
}

_ASSET_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
}


async def collect_reddit(client: httpx.AsyncClient) -> list[dict]:
    """Collect recent posts from crypto subreddits."""
    items = []
    seen_subs = set()

    for asset, subs in SUBREDDITS.items():
        for sub in subs:
            if sub in seen_subs:
                continue
            seen_subs.add(sub)
            try:
                resp = await client.get(
                    f"https://www.reddit.com/r/{sub}/new.json?limit=25",
                    headers={"User-Agent": "Mozilla/5.0 (compatible; academic-research/2.0)"},
                    timeout=15,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    logger.debug("reddit r/%s: status %d", sub, resp.status_code)
                    continue

                for post in resp.json().get("data", {}).get("children", []):
                    d = post.get("data", {})
                    title = d.get("title", "")
                    selftext = (d.get("selftext", "") or "")[:500]
                    score = d.get("score", 0)
                    created = d.get("created_utc", 0)
                    src_id = d.get("id", "")

                    # Detect which assets this post is about
                    text_lower = (title + " " + selftext).lower()
                    post_assets = []
                    for a, keywords in _ASSET_KEYWORDS.items():
                        if any(kw in text_lower for kw in keywords):
                            post_assets.append(a)
                    if not post_assets:
                        continue  # not crypto-relevant

                    for a in post_assets:
                        items.append({
                            "id": hashlib.sha256(f"reddit:{src_id}:{a}".encode()).hexdigest()[:16],
                            "asset": a,
                            "timestamp": datetime.utcfromtimestamp(created).isoformat() if created else datetime.now(timezone.utc).isoformat(),
                            "source": "reddit",
                            "source_id": src_id,
                            "title": title[:200],
                            "body": selftext if selftext else None,
                            "community_score": min(score / 100, 1.0) if score > 0 else max(score / 50, -1.0),
                            "metadata": {"subreddit": sub, "upvote_ratio": d.get("upvote_ratio"), "num_comments": d.get("num_comments", 0)},
                        })
            except Exception as e:
                logger.warning("reddit r/%s: %s", sub, str(e)[:100])

    logger.info("reddit: collected %d items from %d subreddits", len(items), len(seen_subs))
    return items
