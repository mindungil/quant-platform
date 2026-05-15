"""Momentum-based trading formulas — best in trending markets."""
from __future__ import annotations
import math
from shared.formulas.base import BaseFormula, FormulaResult
from shared.formulas.registry import formula_registry


class EMAcrossFormula(BaseFormula):
    name = "momentum_ema_cross"
    description = "EMA(9) vs EMA(21) crossover with ATR-normalized distance"
    best_regime = "trending"
    required_indicators = ["ema_9", "ema_21", "atr_14"]

    def compute(self, features: dict) -> FormulaResult:
        ema_9 = features.get("ema_9")
        ema_21 = features.get("ema_21")
        atr = features.get("atr_14") or 1.0
        if ema_9 is None or ema_21 is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        distance = ema_9 - ema_21
        score = math.tanh(distance / (atr * 2))
        confidence = min(abs(distance) / atr, 1.0) if atr > 0 else 0.0
        return FormulaResult(
            score=max(-1.0, min(1.0, score)),
            confidence=confidence,
            components={"ema_9": ema_9, "ema_21": ema_21, "distance": distance},
            formula_name=self.name,
        )


class MACDHistogramFormula(BaseFormula):
    name = "macd_histogram"
    description = "MACD histogram momentum normalized by ATR"
    best_regime = "trending"
    required_indicators = ["macd", "macd_signal", "atr_14"]

    def compute(self, features: dict) -> FormulaResult:
        macd = features.get("macd")
        signal = features.get("macd_signal")
        atr = features.get("atr_14") or 1.0
        if macd is None or signal is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        histogram = macd - signal
        score = math.tanh(histogram / atr)
        confidence = min(abs(histogram) / atr, 1.0) if atr > 0 else 0.0
        return FormulaResult(
            score=max(-1.0, min(1.0, score)),
            confidence=confidence,
            components={"macd": macd, "signal": signal, "histogram": histogram},
            formula_name=self.name,
        )


class StochasticMomentumFormula(BaseFormula):
    name = "stochastic_momentum"
    description = "Stochastic K/D crossover in trend direction (ADX>25)"
    best_regime = "trending"
    required_indicators = ["stochastic_k", "stochastic_d", "adx_14"]

    def compute(self, features: dict) -> FormulaResult:
        k = features.get("stochastic_k")
        d = features.get("stochastic_d")
        adx = features.get("adx_14", 0)
        if k is None or d is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        # Stochastic signal
        stoch_score = (k - 50) / 50  # normalize to [-1, 1]

        # Only trust in trending markets (ADX > 25)
        trend_confidence = min(max((adx - 15) / 25, 0.0), 1.0) if adx else 0.5
        score = stoch_score * trend_confidence

        return FormulaResult(
            score=max(-1.0, min(1.0, score)),
            confidence=trend_confidence,
            components={"stochastic_k": k, "stochastic_d": d, "adx": adx},
            formula_name=self.name,
        )


formula_registry.register(EMAcrossFormula())
formula_registry.register(MACDHistogramFormula())
formula_registry.register(StochasticMomentumFormula())
