"""Mean reversion factors."""
from __future__ import annotations
import math
from shared.factors.base import Factor


class BBContrarian(Factor):
    """Contrarian signal from Bollinger %B."""

    def __init__(self):
        super().__init__(
            name="bb_contrarian",
            category="reversion",
            description="-(bollinger %B - 0.5) * 2 (contrarian to bands)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        bb_upper = self._safe_get(features, "bb_upper")
        bb_lower = self._safe_get(features, "bb_lower")
        width = bb_upper - bb_lower
        if width <= 0 or close == 0.0:
            return 0.0
        pct_b = (close - bb_lower) / width
        return max(-1.0, min(1.0, -(pct_b - 0.5) * 2.0))


class VWAPReversion(Factor):
    """Contrarian signal from VWAP distance."""

    def __init__(self):
        super().__init__(
            name="vwap_reversion",
            category="reversion",
            description="-(close - VWAP) / ATR (contrarian to VWAP)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        vwap = self._safe_get(features, "vwap")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or vwap == 0.0:
            return 0.0
        return self._tanh_norm(-(close - vwap), atr)


class RSIExtreme(Factor):
    """Contrarian RSI signal at extremes (>80 or <20)."""

    def __init__(self):
        super().__init__(
            name="rsi_extreme",
            category="reversion",
            description="Strong contrarian signal when RSI > 80 or < 20",
        )

    def compute(self, features: dict) -> float:
        rsi = self._safe_get(features, "rsi_14")
        if rsi == 0.0:
            return 0.0
        # Contrarian: high RSI -> sell signal, low RSI -> buy signal
        # Stronger at extremes, weak near 50
        deviation = rsi - 50.0
        # Apply cubic scaling to emphasize extremes
        normalized = deviation / 50.0  # [-1, 1]
        extreme_weight = normalized ** 2  # [0, 1], higher at extremes
        # Contrarian: negate the direction
        return max(-1.0, min(1.0, -normalized * (0.5 + 0.5 * extreme_weight)))


class StochExtreme(Factor):
    """Contrarian stochastic signal at extremes."""

    def __init__(self):
        super().__init__(
            name="stoch_extreme",
            category="reversion",
            description="Contrarian when stoch_k > 80 or < 20",
        )

    def compute(self, features: dict) -> float:
        stoch_k = self._safe_get(features, "stochastic_k")
        if stoch_k == 0.0:
            return 0.0
        deviation = stoch_k - 50.0
        normalized = deviation / 50.0
        extreme_weight = normalized ** 2
        return max(-1.0, min(1.0, -normalized * (0.5 + 0.5 * extreme_weight)))


class SMAReversion(Factor):
    """Contrarian signal from SMA20 distance."""

    def __init__(self):
        super().__init__(
            name="sma_reversion",
            category="reversion",
            description="-(close - SMA20) / ATR (contrarian to SMA)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        sma = self._safe_get(features, "sma_20")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or sma == 0.0:
            return 0.0
        return self._tanh_norm(-(close - sma), atr)


class EMASpread(Factor):
    """Negative of EMA9 - EMA21 spread (reversion)."""

    def __init__(self):
        super().__init__(
            name="ema_spread",
            category="reversion",
            description="-(EMA9 - EMA21) spread normalized by ATR",
        )

    def compute(self, features: dict) -> float:
        ema9 = self._safe_get(features, "ema_9")
        ema21 = self._safe_get(features, "ema_21")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or ema9 == 0.0 or ema21 == 0.0:
            return 0.0
        return self._tanh_norm(-(ema9 - ema21), atr)


class PriceVsRange(Factor):
    """Where is close in today's high-low range? Contrarian."""

    def __init__(self):
        super().__init__(
            name="price_vs_range",
            category="reversion",
            description="Contrarian position within high-low range",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        high = self._safe_get(features, "high")
        low = self._safe_get(features, "low")
        range_size = high - low
        if range_size <= 0 or close == 0.0:
            return 0.0
        # Position in range: 0 = at low, 1 = at high
        position = (close - low) / range_size
        # Contrarian: negate so high in range -> sell, low in range -> buy
        return max(-1.0, min(1.0, -(position * 2.0 - 1.0)))


class BBWidthSqueeze(Factor):
    """BB width squeeze: low width -> neutral (expect breakout), high width -> reversion signal."""

    def __init__(self):
        super().__init__(
            name="bb_width_squeeze",
            category="reversion",
            description="BB width squeeze: low width = 0, high width = reversion expectation",
        )

    def compute(self, features: dict) -> float:
        bb_upper = self._safe_get(features, "bb_upper")
        bb_lower = self._safe_get(features, "bb_lower")
        close = self._safe_get(features, "close")
        if close <= 0:
            return 0.0
        bb_mid = (bb_upper + bb_lower) / 2.0
        if bb_mid <= 0:
            return 0.0
        width = (bb_upper - bb_lower) / bb_mid
        # Typical BB width for crypto: 0.02-0.10
        # Low width (squeeze): return 0 (no reversion signal, expect breakout)
        # High width: return contrarian signal based on price position
        if width < 0.03:
            return 0.0  # Squeeze -- no reversion signal
        # Wider bands -> stronger reversion expectation
        width_factor = min(1.0, (width - 0.03) / 0.07)
        # Direction: contrarian to price position within bands
        if bb_upper == bb_lower:
            return 0.0
        position = (close - bb_lower) / (bb_upper - bb_lower)
        contrarian = -(position * 2.0 - 1.0)
        return max(-1.0, min(1.0, contrarian * width_factor))


MEAN_REVERSION_FACTORS = [
    BBContrarian(),
    VWAPReversion(),
    RSIExtreme(),
    StochExtreme(),
    SMAReversion(),
    EMASpread(),
    PriceVsRange(),
    BBWidthSqueeze(),
]
