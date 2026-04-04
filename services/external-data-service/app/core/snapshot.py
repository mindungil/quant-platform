import logging
import time
from datetime import datetime, timezone

import httpx

from app.models.external_data import ExternalContextSnapshot

logger = logging.getLogger("external-data-service")

_cache: dict = {}  # {cache_key: (data, expires_at)}


def _get_cached(key: str):
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _set_cached(key: str, data, ttl_seconds: int):
    _cache[key] = (data, time.time() + ttl_seconds)


_FEAR_GREED_SENTIMENT = {
    "Extreme Fear": -0.8,
    "Fear": -0.4,
    "Neutral": 0.0,
    "Greed": 0.4,
    "Extreme Greed": 0.8,
}


def _fetch_fear_greed() -> tuple[int, float]:
    """Returns (index_value 0-100, normalized -1 to 1)."""
    cached = _get_cached("fear_greed")
    if cached is not None:
        return cached

    try:
        resp = httpx.get(
            "https://api.alternative.me/fng/?limit=1", timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"][0]
        value = int(data["value"])
        classification = data.get("value_classification", "Neutral")
        normalized = _FEAR_GREED_SENTIMENT.get(classification, 0.0)
        result = (value, normalized)
        _set_cached("fear_greed", result, 600)  # 10 min
        return result
    except Exception as exc:
        logger.warning("Fear & Greed API failed: %s", exc)
        return (50, 0.0)


def _extract_symbol(asset: str) -> str:
    """Strip USDT/KRW suffix to get base symbol."""
    for suffix in ("USDT", "KRW"):
        if asset.upper().endswith(suffix):
            return asset.upper()[: -len(suffix)]
    return asset.upper()


def _fetch_news_sentiment(asset: str) -> float:
    """Returns sentiment -1 to 1."""
    symbol = _extract_symbol(asset)
    cache_key = f"news_{symbol}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        resp = httpx.get(
            "https://cryptopanic.com/api/free/v1/posts/",
            params={
                "public": "true",
                "currencies": symbol,
                "kind": "news",
                "limit": "10",
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            _set_cached(cache_key, 0.0, 300)
            return 0.0

        total_pos = 0
        total_neg = 0
        for post in results:
            votes = post.get("votes", {})
            total_pos += votes.get("positive", 0)
            total_neg += votes.get("negative", 0)

        total = total_pos + total_neg
        sentiment = (total_pos - total_neg) / max(total, 1)
        sentiment = max(-1.0, min(1.0, sentiment))
        _set_cached(cache_key, sentiment, 300)  # 5 min
        return sentiment
    except Exception as exc:
        logger.warning("CryptoPanic API failed: %s", exc)
        return 0.0


def _fetch_onchain_score(asset: str) -> float:
    """Returns score -1 to 1."""
    symbol = _extract_symbol(asset)
    cache_key = f"onchain_{symbol}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    if symbol != "BTC":
        _set_cached(cache_key, 0.0, 900)
        return 0.0

    try:
        resp = httpx.get(
            "https://api.blockchain.info/stats", timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        n_tx = data.get("n_tx", 0)
        score = min(n_tx / 400_000, 1.0) * 2 - 1
        score = max(-1.0, min(1.0, score))
        _set_cached(cache_key, score, 900)  # 15 min
        return score
    except Exception as exc:
        logger.warning("Blockchain.info API failed: %s", exc)
        return 0.0


def build_external_context(asset: str) -> ExternalContextSnapshot:
    missing: list[str] = []

    fear_greed_int, fear_greed_norm = _fetch_fear_greed()
    news = _fetch_news_sentiment(asset)
    onchain = _fetch_onchain_score(asset)
    macro = -fear_greed_norm  # inverse: extreme greed = high macro risk

    return ExternalContextSnapshot(
        asset=asset,
        timestamp=datetime.now(timezone.utc),
        news_sentiment=news,
        onchain_score=onchain,
        macro_risk_score=macro,
        fear_greed_index=fear_greed_int,
        components={
            "news_sentiment": news,
            "onchain_score": onchain,
            "macro_risk_score": macro,
            "fear_greed_bias": round((fear_greed_int - 50) / 50, 4),
        },
        missing_fields=missing,
    )
