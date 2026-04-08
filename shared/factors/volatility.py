"""Volatility factors."""
from __future__ import annotations
import math
from shared.factors.base import Factor


class ATRRelative(Factor):
    """ATR relative to close price."""

    def __init__(self):
        super().__init__(
            name="atr_relative",
            category="volatility",
            description="ATR / close (higher = more volatile)",
        )

    def compute(self, features: dict) -> float:
        atr = self._safe_get(features, "atr_14")
        close = self._safe_get(features, "close")
        if close <= 0:
            return 0.0
        ratio = atr / close
        # Normalize: typical crypto ATR/close 0.01-0.05, scale so 0.03 ~ 0.5
        return self._tanh_norm(ratio, 0.03)


class BBWidth(Factor):
    """Bollinger Band width relative to middle band."""

    def __init__(self):
        super().__init__(
            name="bb_width",
            category="volatility",
            description="(BB_upper - BB_lower) / BB_middle",
        )

    def compute(self, features: dict) -> float:
        bb_upper = self._safe_get(features, "bb_upper")
        bb_lower = self._safe_get(features, "bb_lower")
        bb_mid = (bb_upper + bb_lower) / 2.0
        if bb_mid <= 0:
            return 0.0
        width = (bb_upper - bb_lower) / bb_mid
        # Normalize: typical 0.02-0.10, scale so 0.06 ~ 0.5
        return self._tanh_norm(width, 0.06)


class VolatilityTrend(Factor):
    """Is volatility increasing or decreasing? Approximated from ATR vs close range."""

    def __init__(self):
        super().__init__(
            name="volatility_trend",
            category="volatility",
            description="Volatility trend: ATR relative to recent range",
        )

    def compute(self, features: dict) -> float:
        atr = self._safe_get(features, "atr_14")
        high = self._safe_get(features, "high")
        low = self._safe_get(features, "low")
        close = self._safe_get(features, "close")
        if close <= 0 or atr <= 0:
            return 0.0
        current_range = high - low
        if current_range <= 0:
            return 0.0
        # If current bar range > ATR, volatility is expanding
        # If current bar range < ATR, volatility is contracting
        ratio = current_range / atr
        # ratio > 1 -> expanding, < 1 -> contracting
        return self._tanh_norm(ratio - 1.0, 0.5)


class RangeExpansion(Factor):
    """Current bar range relative to ATR."""

    def __init__(self):
        super().__init__(
            name="range_expansion",
            category="volatility",
            description="(high - low) / ATR: 1.0 = normal, >1.5 = expansion",
        )

    def compute(self, features: dict) -> float:
        high = self._safe_get(features, "high")
        low = self._safe_get(features, "low")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0:
            return 0.0
        bar_range = high - low
        ratio = bar_range / atr
        # Normalize: 1.0 is normal (-> 0), >1.5 is expansion (-> positive)
        return self._tanh_norm(ratio - 1.0, 0.5)


class SqueezeIndicator(Factor):
    """Simplified squeeze indicator: is BB width below threshold?"""

    def __init__(self):
        super().__init__(
            name="squeeze_indicator",
            category="volatility",
            description="BB width squeeze: narrow bands -> 1 (squeeze), wide -> -1",
        )

    def compute(self, features: dict) -> float:
        bb_upper = self._safe_get(features, "bb_upper")
        bb_lower = self._safe_get(features, "bb_lower")
        bb_mid = (bb_upper + bb_lower) / 2.0
        if bb_mid <= 0:
            return 0.0
        width = (bb_upper - bb_lower) / bb_mid
        # Squeeze threshold: below 0.03 is tight squeeze
        # Normal: 0.04-0.06, Wide: > 0.08
        # Map: narrow -> +1 (squeeze on), wide -> -1 (no squeeze)
        return self._tanh_norm(-(width - 0.05), 0.03)


class VolumeVolatility(Factor):
    """Volume relative to typical volume."""

    def __init__(self):
        super().__init__(
            name="volume_volatility",
            category="volatility",
            description="Volume relative to average (volume / typical_volume)",
        )

    def compute(self, features: dict) -> float:
        volume = self._safe_get(features, "volume")
        typical_volume = self._safe_get(features, "typical_volume")
        if typical_volume <= 0:
            # No baseline available
            return 0.0
        ratio = volume / typical_volume
        # ratio > 1 -> above average, < 1 -> below average
        # Normalize: 2x volume -> ~0.76, 0.5x -> ~-0.46
        return self._tanh_norm(ratio - 1.0, 1.0)


VOLATILITY_FACTORS = [
    ATRRelative(),
    BBWidth(),
    VolatilityTrend(),
    RangeExpansion(),
    SqueezeIndicator(),
    VolumeVolatility(),
]
