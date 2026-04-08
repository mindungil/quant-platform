"""Technical indicator factors."""
from __future__ import annotations
import math
from shared.factors.base import Factor


class RSILevel(Factor):
    """RSI normalized to [-1, 1]: (RSI - 50) / 50."""

    def __init__(self):
        super().__init__(
            name="rsi_level",
            category="technical",
            description="RSI normalized: (RSI - 50) / 50",
        )

    def compute(self, features: dict) -> float:
        rsi = self._safe_get(features, "rsi_14")
        if rsi == 0.0:
            return 0.0
        return max(-1.0, min(1.0, (rsi - 50.0) / 50.0))


class MACDHistogram(Factor):
    """MACD histogram normalized by ATR via tanh."""

    def __init__(self):
        super().__init__(
            name="macd_histogram",
            category="technical",
            description="MACD histogram / ATR via tanh",
        )

    def compute(self, features: dict) -> float:
        macd = self._safe_get(features, "macd")
        signal = self._safe_get(features, "macd_signal")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0:
            return 0.0
        histogram = macd - signal
        return self._tanh_norm(histogram, atr)


class BollingerPctB(Factor):
    """Bollinger %B mapped to [-1, 1]."""

    def __init__(self):
        super().__init__(
            name="bollinger_pctb",
            category="technical",
            description="Bollinger %B: (close - bb_lower) / (bb_upper - bb_lower) * 2 - 1",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        bb_upper = self._safe_get(features, "bb_upper")
        bb_lower = self._safe_get(features, "bb_lower")
        width = bb_upper - bb_lower
        if width <= 0 or close == 0.0:
            return 0.0
        pct_b = (close - bb_lower) / width
        return max(-1.0, min(1.0, pct_b * 2.0 - 1.0))


class StochasticLevel(Factor):
    """Stochastic %K normalized to [-1, 1]."""

    def __init__(self):
        super().__init__(
            name="stochastic_level",
            category="technical",
            description="(stoch_k - 50) / 50",
        )

    def compute(self, features: dict) -> float:
        stoch_k = self._safe_get(features, "stochastic_k")
        if stoch_k == 0.0:
            return 0.0
        return max(-1.0, min(1.0, (stoch_k - 50.0) / 50.0))


class ADXStrength(Factor):
    """ADX trend strength normalized to [0, 1]."""

    def __init__(self):
        super().__init__(
            name="adx_strength",
            category="technical",
            description="ADX / 50 clamped to [0, 1] (trend strength, not direction)",
        )

    def compute(self, features: dict) -> float:
        adx = self._safe_get(features, "adx_14")
        if adx == 0.0:
            return 0.0
        return max(0.0, min(1.0, adx / 50.0))


class ATRPercentile(Factor):
    """ATR relative to close, normalized via tanh."""

    def __init__(self):
        super().__init__(
            name="atr_percentile",
            category="technical",
            description="ATR relative to close: (ATR / close) normalized",
        )

    def compute(self, features: dict) -> float:
        atr = self._safe_get(features, "atr_14")
        close = self._safe_get(features, "close")
        if close <= 0:
            return 0.0
        ratio = atr / close
        # Typical crypto ATR/close is 0.01-0.05; scale so 0.03 ~ 0.5
        return self._tanh_norm(ratio, 0.03)


class EMAAlignment(Factor):
    """EMA alignment: EMA9 > EMA21 > EMA50 -> +1, reverse -> -1."""

    def __init__(self):
        super().__init__(
            name="ema_alignment",
            category="technical",
            description="EMA ordering: bullish alignment +1, bearish -1",
        )

    def compute(self, features: dict) -> float:
        ema9 = self._safe_get(features, "ema_9")
        ema21 = self._safe_get(features, "ema_21")
        ema50 = self._safe_get(features, "ema_50")
        if ema9 == 0.0 or ema21 == 0.0 or ema50 == 0.0:
            return 0.0
        score = 0.0
        if ema9 > ema21:
            score += 0.5
        else:
            score -= 0.5
        if ema21 > ema50:
            score += 0.5
        else:
            score -= 0.5
        return score


class SMADistance(Factor):
    """Distance from SMA20 normalized by ATR via tanh."""

    def __init__(self):
        super().__init__(
            name="sma_distance",
            category="technical",
            description="(close - SMA20) / ATR via tanh",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        sma = self._safe_get(features, "sma_20")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or sma == 0.0:
            return 0.0
        return self._tanh_norm(close - sma, atr)


class VWAPDistance(Factor):
    """Distance from VWAP normalized by ATR via tanh."""

    def __init__(self):
        super().__init__(
            name="vwap_distance",
            category="technical",
            description="(close - VWAP) / ATR via tanh",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        vwap = self._safe_get(features, "vwap")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or vwap == 0.0:
            return 0.0
        return self._tanh_norm(close - vwap, atr)


class OBVMomentum(Factor):
    """OBV direction signal using sign and volume context."""

    def __init__(self):
        super().__init__(
            name="obv_momentum",
            category="technical",
            description="OBV direction (sign * volume context)",
        )

    def compute(self, features: dict) -> float:
        obv = self._safe_get(features, "obv")
        close = self._safe_get(features, "close")
        volume = self._safe_get(features, "volume")
        if close <= 0 or volume <= 0:
            return 0.0
        # Normalize OBV by (close * volume) to get a dimensionless signal
        typical_obv_scale = close * volume
        if typical_obv_scale <= 0:
            return 0.0
        return self._tanh_norm(obv, typical_obv_scale)


TECHNICAL_FACTORS = [
    RSILevel(),
    MACDHistogram(),
    BollingerPctB(),
    StochasticLevel(),
    ADXStrength(),
    ATRPercentile(),
    EMAAlignment(),
    SMADistance(),
    VWAPDistance(),
    OBVMomentum(),
]
