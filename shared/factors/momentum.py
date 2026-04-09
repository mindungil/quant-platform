"""Momentum factors."""
from __future__ import annotations
import math
from shared.factors.base import Factor


class EMACross9_21(Factor):
    """EMA 9/21 crossover signal."""

    def __init__(self):
        super().__init__(
            name="ema_cross_9_21",
            category="momentum",
            description="tanh((EMA9 - EMA21) / ATR)",
        )

    def compute(self, features: dict) -> float:
        ema9 = self._safe_get(features, "ema_9")
        ema21 = self._safe_get(features, "ema_21")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or ema9 == 0.0 or ema21 == 0.0:
            return 0.0
        return self._tanh_norm(ema9 - ema21, atr)


class EMACross21_50(Factor):
    """EMA 21/50 crossover signal."""

    def __init__(self):
        super().__init__(
            name="ema_cross_21_50",
            category="momentum",
            description="tanh((EMA21 - EMA50) / ATR)",
        )

    def compute(self, features: dict) -> float:
        ema21 = self._safe_get(features, "ema_21")
        ema50 = self._safe_get(features, "ema_50")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or ema21 == 0.0 or ema50 == 0.0:
            return 0.0
        return self._tanh_norm(ema21 - ema50, atr)


class MACDCrossover(Factor):
    """MACD vs signal line crossover."""

    def __init__(self):
        super().__init__(
            name="macd_crossover",
            category="momentum",
            description="tanh((MACD - signal) / ATR)",
        )

    def compute(self, features: dict) -> float:
        macd = self._safe_get(features, "macd")
        signal = self._safe_get(features, "macd_signal")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0:
            return 0.0
        return self._tanh_norm(macd - signal, atr)


class StochMomentum(Factor):
    """Stochastic momentum weighted by ADX confidence."""

    def __init__(self):
        super().__init__(
            name="stoch_momentum",
            category="momentum",
            description="(stoch_k - stoch_d) / 50 * ADX_confidence",
        )

    def compute(self, features: dict) -> float:
        stoch_k = self._safe_get(features, "stochastic_k")
        stoch_d = self._safe_get(features, "stochastic_d")
        adx = self._safe_get(features, "adx_14")
        if stoch_k == 0.0 and stoch_d == 0.0:
            return 0.0
        # ADX confidence: 0 at ADX=0, 1 at ADX>=50
        adx_confidence = min(1.0, adx / 50.0) if adx > 0 else 0.5
        raw = (stoch_k - stoch_d) / 50.0
        return max(-1.0, min(1.0, raw * adx_confidence))


class RSIMomentum(Factor):
    """RSI rate of change (uses rsi_prev if available, else approximates from RSI distance to 50)."""

    def __init__(self):
        super().__init__(
            name="rsi_momentum",
            category="momentum",
            description="RSI rate of change",
        )

    def compute(self, features: dict) -> float:
        rsi = self._safe_get(features, "rsi_14")
        rsi_prev = self._safe_get(features, "rsi_prev")
        if rsi == 0.0:
            return 0.0
        if rsi_prev != 0.0:
            # Direct rate of change
            delta = rsi - rsi_prev
            return max(-1.0, min(1.0, delta / 20.0))
        # Approximate: RSI momentum from distance to neutral
        # Strong RSI moving away from 50 suggests momentum
        return self._tanh_norm(rsi - 50.0, 30.0)


class PriceMomentumShort(Factor):
    """Short-term price momentum: close vs EMA9."""

    def __init__(self):
        super().__init__(
            name="price_momentum_short",
            category="momentum",
            description="close vs EMA9 normalized by ATR",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        ema9 = self._safe_get(features, "ema_9")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or ema9 == 0.0:
            return 0.0
        return self._tanh_norm(close - ema9, atr)


class PriceMomentumMedium(Factor):
    """Medium-term price momentum: close vs EMA21."""

    def __init__(self):
        super().__init__(
            name="price_momentum_medium",
            category="momentum",
            description="close vs EMA21 normalized by ATR",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        ema21 = self._safe_get(features, "ema_21")
        atr = self._safe_get(features, "atr_14")
        if atr <= 0 or ema21 == 0.0:
            return 0.0
        return self._tanh_norm(close - ema21, atr)


class TrendConsistency(Factor):
    """Count how many MAs point in the same direction relative to price."""

    def __init__(self):
        super().__init__(
            name="trend_consistency",
            category="momentum",
            description="Fraction of MAs agreeing on direction",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        if close == 0.0:
            return 0.0
        ma_keys = ["ema_9", "ema_21", "ema_50", "sma_20", "vwap"]
        above = 0
        total = 0
        for key in ma_keys:
            ma_val = self._safe_get(features, key)
            if ma_val > 0:
                total += 1
                if close > ma_val:
                    above += 1
        if total == 0:
            return 0.0
        # Map [0, total] to [-1, 1]
        return (above / total) * 2.0 - 1.0


MOMENTUM_FACTORS = [
    EMACross9_21(),
    EMACross21_50(),
    MACDCrossover(),
    StochMomentum(),
    RSIMomentum(),
    PriceMomentumShort(),
    PriceMomentumMedium(),
    TrendConsistency(),
]
