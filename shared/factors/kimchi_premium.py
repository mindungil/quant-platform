"""Kimchi Premium Factor — Korean exchange premium detection.

Compares Upbit KRW-BTC vs Binance USDT-BTC after USD/KRW conversion.
When Korean price is significantly higher → bearish (premium will compress)
When Korean price is significantly lower → bullish (discount opportunity)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from shared.factors.base import Factor

logger = logging.getLogger("kimchi-premium")

# Default fallback FX rate (KRW per USD) — used when the public FX API fails.
DEFAULT_USDKRW = 1380.0

# Simple in-process cache to avoid hammering public APIs.
# Kimchi premium moves on the order of seconds, so a short TTL is enough.
_CACHE_TTL_SECONDS = 30.0
_FX_CACHE_TTL_SECONDS = 300.0  # FX moves slowly; 5 minutes is fine
_cache: dict[str, tuple[float, dict]] = {}
_fx_cache: dict[str, tuple[float, float]] = {}


def fetch_usdkrw() -> float:
    """Fetch current USD/KRW rate from a free public API (cached)."""
    now = time.time()
    cached = _fx_cache.get("USDKRW")
    if cached and now - cached[0] < _FX_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        resp = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            rate = data.get("rates", {}).get("KRW")
            if rate:
                rate_f = float(rate)
                _fx_cache["USDKRW"] = (now, rate_f)
                return rate_f
    except Exception as exc:
        logger.debug("fetch_usdkrw failed: %s", exc)

    # Fall back to last cached value if available, else default
    if cached:
        return cached[1]
    return DEFAULT_USDKRW


def fetch_upbit_krw_price(asset: str = "BTC") -> Optional[float]:
    """Fetch current Upbit KRW price for an asset."""
    try:
        resp = httpx.get(
            f"https://api.upbit.com/v1/ticker?markets=KRW-{asset}",
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data and len(data) > 0:
                price = data[0].get("trade_price", 0)
                return float(price) if price else None
    except Exception as exc:
        logger.debug("fetch_upbit_krw_price failed: %s", exc)
    return None


def fetch_binance_usdt_price(asset: str = "BTC") -> Optional[float]:
    """Fetch current Binance USDT price for an asset."""
    try:
        resp = httpx.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT",
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("price", 0)
            return float(price) if price else None
    except Exception as exc:
        logger.debug("fetch_binance_usdt_price failed: %s", exc)
    return None


def compute_kimchi_premium(asset: str = "BTC") -> dict:
    """Compute current kimchi premium for an asset.

    Returns a dict with keys:
        premium_pct:   positive = Korean price higher
        krw_price:     Upbit trade price in KRW
        usdt_price:    Binance trade price in USDT
        usdkrw_rate:   USD/KRW FX rate
        krw_equivalent: USDT price converted to KRW
    """
    asset = (asset or "BTC").upper()

    now = time.time()
    cached = _cache.get(asset)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    krw_price = fetch_upbit_krw_price(asset)
    usdt_price = fetch_binance_usdt_price(asset)
    fx_rate = fetch_usdkrw()

    if not krw_price or not usdt_price:
        result = {
            "premium_pct": 0.0,
            "krw_price": 0,
            "usdt_price": 0,
            "usdkrw_rate": fx_rate,
            "krw_equivalent": 0,
            "error": "price_fetch_failed",
        }
        # Don't cache failures for long — still cache briefly to avoid retry storms.
        _cache[asset] = (now, result)
        return result

    krw_equivalent = usdt_price * fx_rate
    premium_pct = ((krw_price - krw_equivalent) / krw_equivalent) * 100

    result = {
        "premium_pct": round(premium_pct, 4),
        "krw_price": krw_price,
        "usdt_price": usdt_price,
        "usdkrw_rate": fx_rate,
        "krw_equivalent": round(krw_equivalent, 2),
    }
    _cache[asset] = (now, result)
    return result


class KimchiPremiumFactor(Factor):
    """Factor that uses kimchi premium as a contrarian signal.

    High premium (Korean over global) → bearish (premium will compress)
    Negative premium (Korean under global) → bullish (discount)
    """

    def __init__(self) -> None:
        super().__init__(
            name="kimchi_premium",
            category="sentiment",
            description="김치 프리미엄 — 한국 vs 글로벌 가격 차이 (역발상)",
        )

    def compute(self, features: dict) -> float:
        # Get from features (cached upstream) or fetch fresh
        kimchi = features.get("kimchi_premium_pct")
        if kimchi is None:
            asset_raw = features.get("asset", "BTCUSDT") or "BTCUSDT"
            asset = (
                asset_raw.replace("USDT", "")
                .replace("KRW-", "")
                .replace("-KRW", "")
                .upper()
            )
            try:
                data = compute_kimchi_premium(asset)
                kimchi = data.get("premium_pct", 0.0)
            except Exception as exc:
                logger.debug("KimchiPremiumFactor.compute failed: %s", exc)
                return 0.0

        if kimchi is None:
            return 0.0

        # Contrarian signal:
        #  +5% premium → strong sell (~ -1.0)
        #   0% → neutral (0)
        #  -5% discount → strong buy (~ +1.0)
        try:
            return self._tanh_norm(-float(kimchi), scale=3.0)
        except (TypeError, ValueError):
            return 0.0


# Module-level instances
KIMCHI_PREMIUM_FACTORS = [KimchiPremiumFactor()]
