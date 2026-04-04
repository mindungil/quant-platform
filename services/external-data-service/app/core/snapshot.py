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


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _extract_symbol(asset: str) -> str:
    """Strip USDT/KRW suffix to get base symbol."""
    for suffix in ("USDT", "KRW"):
        if asset.upper().endswith(suffix):
            return asset.upper()[: -len(suffix)]
    return asset.upper()


# ---------------------------------------------------------------------------
# 1. Fear & Greed Index
# ---------------------------------------------------------------------------

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
        normalized = _clamp((value - 50) / 50)
        result = (value, normalized)
        _set_cached("fear_greed", result, 600)  # 10 min
        return result
    except Exception as exc:
        logger.warning("FearGreed fetch failed: %s", exc)
        return (50, 0.0)


# ---------------------------------------------------------------------------
# 2. CoinGecko Community Sentiment + price_change_24h
# ---------------------------------------------------------------------------

_COINGECKO_COIN_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


def _fetch_coingecko_sentiment(asset: str) -> tuple[float, float | None]:
    """Returns (sentiment -1..1, price_change_24h percent or None)."""
    symbol = _extract_symbol(asset)
    cache_key = f"cg_sent_{symbol}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    coin_id = _COINGECKO_COIN_MAP.get(symbol, symbol.lower())
    try:
        resp = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "false",
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()

        pct_up = data.get("sentiment_votes_up_percentage")
        sentiment = _clamp((pct_up - 50) / 50) if pct_up is not None else 0.0

        md = data.get("market_data") or {}
        price_chg = md.get("price_change_percentage_24h")

        result = (sentiment, price_chg)
        _set_cached(cache_key, result, 300)  # 5 min
        return result
    except Exception as exc:
        logger.warning("CoinGecko sentiment fetch failed: %s", exc)
        return (0.0, None)


# ---------------------------------------------------------------------------
# 3. CoinGecko Global Market Dominance
# ---------------------------------------------------------------------------

def _fetch_global_dominance() -> tuple[float | None, float | None, bool | None]:
    """Returns (btc_dominance, market_cap_change_24h_usd, altcoin_season)."""
    cached = _get_cached("cg_global")
    if cached is not None:
        return cached

    try:
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/global", timeout=8.0,
        )
        resp.raise_for_status()
        gdata = resp.json().get("data", {})

        btc_dom = gdata.get("market_cap_percentage", {}).get("btc")
        mc_chg = gdata.get("market_cap_change_percentage_24h_usd")
        altcoin_season = btc_dom < 45 if btc_dom is not None else None

        result = (btc_dom, mc_chg, altcoin_season)
        _set_cached("cg_global", result, 600)  # 10 min
        return result
    except Exception as exc:
        logger.warning("CoinGecko global fetch failed: %s", exc)
        return (None, None, None)


# ---------------------------------------------------------------------------
# 4. On-chain BTC (enhanced blockchain.info)
# ---------------------------------------------------------------------------

def _fetch_onchain_btc() -> tuple[float, float, float]:
    """Returns (n_tx_score, hash_rate_score, fee_score) each -1..1."""
    cached = _get_cached("onchain_btc")
    if cached is not None:
        return cached

    try:
        resp = httpx.get(
            "https://api.blockchain.info/stats", timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()

        n_tx = data.get("n_tx", 0)
        n_tx_score = _clamp(min(n_tx / 400_000, 1.5) * 2 - 1)

        hash_rate = data.get("hash_rate", 0)
        hash_rate_score = _clamp(min(hash_rate / 500_000_000_000_000, 1.5) * 2 - 1)

        total_fees = data.get("total_fees_btc", 0)
        if total_fees > 0:
            fee_score = _clamp(min(total_fees / 50, 1.5) * 2 - 1)
        else:
            fee_score = 0.0

        result = (n_tx_score, hash_rate_score, fee_score)
        _set_cached("onchain_btc", result, 900)  # 15 min
        return result
    except Exception as exc:
        logger.warning("Blockchain.info fetch failed: %s", exc)
        return (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 5. CoinPaprika Volume Score
# ---------------------------------------------------------------------------

_COINPAPRIKA_ID_MAP = {
    "BTC": "btc-bitcoin",
    "ETH": "eth-ethereum",
    "SOL": "sol-solana",
}

_VOLUME_BASELINE = {
    "BTC": 20_000_000_000,
    "ETH": 8_000_000_000,
    "SOL": 2_000_000_000,
}


def _fetch_coinpaprika_volume(asset: str) -> tuple[float, float | None]:
    """Returns (volume_score -1..1, percent_change_24h or None)."""
    symbol = _extract_symbol(asset)
    cache_key = f"paprika_{symbol}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    ticker_id = _COINPAPRIKA_ID_MAP.get(symbol)
    if ticker_id is None:
        _set_cached(cache_key, (0.0, None), 300)
        return (0.0, None)

    try:
        resp = httpx.get(
            f"https://api.coinpaprika.com/v1/tickers/{ticker_id}",
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()

        quotes = data.get("quotes", {}).get("USD", {})
        pct_chg = quotes.get("percent_change_24h")
        vol_24h = quotes.get("volume_24h", 0)

        baseline = _VOLUME_BASELINE.get(symbol, 2_000_000_000)
        vol_score = _clamp(min(vol_24h / baseline, 2.0) - 1)

        result = (vol_score, pct_chg)
        _set_cached(cache_key, result, 300)  # 5 min
        return result
    except Exception as exc:
        logger.warning("CoinPaprika fetch failed: %s", exc)
        return (0.0, None)


# ---------------------------------------------------------------------------
# Composite builder
# ---------------------------------------------------------------------------

def build_external_context(asset: str) -> ExternalContextSnapshot:
    missing: list[str] = []

    # 1. Fear & Greed
    try:
        fear_greed_int, fear_greed_norm = _fetch_fear_greed()
    except Exception as exc:
        logger.warning("fear_greed source failed: %s", exc)
        fear_greed_int, fear_greed_norm = 50, 0.0
        missing.append("fear_greed")

    # 2. CoinGecko Sentiment
    try:
        coingecko_sentiment, price_change_24h = _fetch_coingecko_sentiment(asset)
    except Exception as exc:
        logger.warning("coingecko_sentiment source failed: %s", exc)
        coingecko_sentiment, price_change_24h = 0.0, None
        missing.append("coingecko_sentiment")

    # 3. CoinGecko Global Dominance
    try:
        btc_dominance, market_cap_change_24h, altcoin_season = _fetch_global_dominance()
    except Exception as exc:
        logger.warning("global_dominance source failed: %s", exc)
        btc_dominance, market_cap_change_24h, altcoin_season = None, None, None
        missing.append("global_dominance")

    # 4. On-chain
    symbol = _extract_symbol(asset)
    if symbol == "BTC":
        try:
            n_tx_score, hash_rate_score, fee_score = _fetch_onchain_btc()
        except Exception as exc:
            logger.warning("onchain_btc source failed: %s", exc)
            n_tx_score, hash_rate_score, fee_score = 0.0, 0.0, 0.0
            missing.append("onchain_btc")
    else:
        n_tx_score, hash_rate_score, fee_score = 0.0, 0.0, 0.0

    # 5. CoinPaprika Volume
    try:
        volume_score, paprika_pct_chg = _fetch_coinpaprika_volume(asset)
    except Exception as exc:
        logger.warning("coinpaprika source failed: %s", exc)
        volume_score, paprika_pct_chg = 0.0, None
        missing.append("coinpaprika")

    # --- Composite scores ---

    # Sentiment composite: FG 40%, CoinGecko 40%, (legacy news placeholder 20% → 0)
    sentiment_composite = _clamp(
        fear_greed_norm * 0.4
        + coingecko_sentiment * 0.4
        + 0.0 * 0.2  # news_sentiment placeholder
    )

    # On-chain composite (BTC only)
    if symbol == "BTC":
        onchain_composite = _clamp(
            n_tx_score * 0.5 + hash_rate_score * 0.3 + fee_score * 0.2
        )
    else:
        onchain_composite = 0.0

    # Macro risk score
    macro_risk_score = -fear_greed_norm
    if btc_dominance is not None and btc_dominance > 60:
        macro_risk_score += 0.2
    if market_cap_change_24h is not None and market_cap_change_24h < -3:
        macro_risk_score += 0.3
    macro_risk_score = _clamp(macro_risk_score)

    return ExternalContextSnapshot(
        asset=asset,
        timestamp=datetime.now(timezone.utc),
        news_sentiment=round(sentiment_composite, 4),
        onchain_score=round(onchain_composite, 4),
        macro_risk_score=round(macro_risk_score, 4),
        fear_greed_index=fear_greed_int,
        btc_dominance=round(btc_dominance, 2) if btc_dominance is not None else None,
        market_cap_change_24h=round(market_cap_change_24h, 4) if market_cap_change_24h is not None else None,
        volume_score=round(volume_score, 4),
        price_change_24h=round(price_change_24h, 4) if price_change_24h is not None else None,
        altcoin_season=altcoin_season,
        components={
            "fear_greed_raw": float(fear_greed_int),
            "fear_greed_norm": round(fear_greed_norm, 4),
            "coingecko_sentiment": round(coingecko_sentiment, 4),
            "btc_dominance": round(btc_dominance, 2) if btc_dominance is not None else 0.0,
            "market_cap_change_24h": round(market_cap_change_24h, 4) if market_cap_change_24h is not None else 0.0,
            "n_tx_score": round(n_tx_score, 4),
            "hash_rate_score": round(hash_rate_score, 4),
            "volume_score": round(volume_score, 4),
            "price_change_24h": round(price_change_24h, 4) if price_change_24h is not None else 0.0,
            "sentiment_composite": round(sentiment_composite, 4),
            "onchain_composite": round(onchain_composite, 4),
            "macro_risk_adjusted": round(macro_risk_score, 4),
        },
        missing_fields=missing,
    )
