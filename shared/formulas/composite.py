"""Composite adaptive formula — production-grade blending of multiple signals.

Features:
- Regime-aware dynamic weighting (momentum signals boosted in trends, reversion in sideways)
- Signal agreement bonus (consensus among 4/5+ signals boosts confidence)
- Recency weighting (more recent indicator signals weighted slightly higher)
- Signal quality weighting (stronger signals get more influence)
- Volatility-adjusted output (high-vol dampens position sizing)
- Trend filter (counter-trend signals suppressed when ADX > 35)
"""
from __future__ import annotations

import math
from shared.formulas.base import BaseFormula, FormulaResult
from shared.formulas.registry import formula_registry
from shared.regime import detect_regime


# ---------------------------------------------------------------------------
# Signal category tags
# ---------------------------------------------------------------------------

_MOMENTUM_SIGNALS = {"macd", "ema_cross", "stochastic"}
_REVERSION_SIGNALS = {"rsi", "bollinger", "vwap"}
_NEUTRAL_SIGNALS = {"sma_20", "obv"}  # contribute to both


class CompositeAdaptiveFormula(BaseFormula):
    name = "composite_adaptive"
    description = (
        "Regime-aware weighted blend of all indicators with agreement bonus, "
        "quality weighting, and volatility adjustment. Default fallback."
    )
    best_regime = "any"
    required_indicators = [
        "rsi_14", "macd", "macd_signal", "sma_20", "vwap", "close",
        "atr_14", "adx_14", "bb_upper", "bb_lower", "ema_9", "ema_21",
        "stochastic_k", "stochastic_d", "obv",
    ]

    def compute(self, features: dict) -> FormulaResult:
        components: dict = {}
        close = features.get("close")
        atr = features.get("atr_14") or (close * 0.01 if close else 0.01)
        adx = features.get("adx_14")

        # Detect regime for dynamic weighting
        regime = detect_regime(features)
        components["regime"] = regime.label

        # ------------------------------------------------------------------
        # 1. Compute individual signal values [-1, 1]
        # ------------------------------------------------------------------
        # Each entry: (name, value, category, base_weight)
        raw_signals: list[tuple[str, float, str, float]] = []

        # RSI — reversion signal
        rsi = features.get("rsi_14")
        if rsi is not None:
            rsi_signal = (rsi - 50) / 50
            raw_signals.append(("rsi", rsi_signal, "reversion", 1.0))

        # MACD histogram — momentum signal
        macd = features.get("macd")
        macd_sig = features.get("macd_signal")
        if macd is not None and macd_sig is not None:
            histogram = macd - macd_sig
            macd_s = math.tanh(histogram / max(atr, 1e-9))
            raw_signals.append(("macd", macd_s, "momentum", 1.0))

        # EMA 9/21 cross — momentum signal
        ema_9 = features.get("ema_9")
        ema_21 = features.get("ema_21")
        if ema_9 is not None and ema_21 is not None and close and close > 0:
            ema_diff = (ema_9 - ema_21) / close
            ema_s = math.tanh(ema_diff * 80)
            raw_signals.append(("ema_cross", ema_s, "momentum", 0.9))

        # SMA 20 distance — neutral/trend signal
        sma = features.get("sma_20")
        if close is not None and sma is not None:
            sma_s = math.tanh((close - sma) / (atr * math.sqrt(20)))
            raw_signals.append(("sma_20", sma_s, "neutral", 0.8))

        # VWAP distance — reversion signal
        vwap = features.get("vwap")
        if close is not None and vwap is not None:
            vwap_s = math.tanh((close - vwap) / (atr * 2))
            raw_signals.append(("vwap", vwap_s, "reversion", 0.9))

        # Bollinger %B — reversion signal
        bb_upper = features.get("bb_upper")
        bb_lower = features.get("bb_lower")
        if close is not None and bb_upper is not None and bb_lower is not None:
            bb_range = bb_upper - bb_lower
            if bb_range > 0:
                pct_b = (close - bb_lower) / bb_range
                bb_s = (pct_b - 0.5) * 2
                raw_signals.append(("bollinger", bb_s, "reversion", 0.9))

        # Stochastic — momentum signal
        stoch_k = features.get("stochastic_k")
        stoch_d = features.get("stochastic_d")
        if stoch_k is not None and stoch_d is not None:
            stoch_s = (stoch_k - 50) / 50
            # Cross bonus
            if stoch_k > stoch_d:
                stoch_s = min(stoch_s + 0.1, 1.0)
            elif stoch_k < stoch_d:
                stoch_s = max(stoch_s - 0.1, -1.0)
            raw_signals.append(("stochastic", stoch_s, "momentum", 0.8))

        # OBV trend — neutral
        obv = features.get("obv")
        if obv is not None and close is not None and close > 0:
            # OBV is cumulative — use sign as directional hint, scale by volume context
            volume = features.get("volume")
            if volume and volume > 0:
                # Normalize OBV relative to recent volume (scale-invariant)
                obv_normalized = obv / (volume * 100)  # rough normalization
                obv_s = math.tanh(obv_normalized * 0.1)
            else:
                obv_s = 0.0
            raw_signals.append(("obv", obv_s, "neutral", 0.5))

        if not raw_signals:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        # ------------------------------------------------------------------
        # 2. Apply regime-aware dynamic weights
        # ------------------------------------------------------------------
        regime_trend = regime.trend_strength
        is_trending = regime_trend == "trending"
        is_sideways = regime_trend == "sideways"

        weighted_signals: list[tuple[str, float, float]] = []  # (name, value, weight)

        for idx, (name, value, category, base_weight) in enumerate(raw_signals):
            w = base_weight

            # Regime multiplier
            if is_trending and category == "momentum":
                w *= 2.0
            elif is_sideways and category == "reversion":
                w *= 2.0
            elif is_trending and category == "reversion":
                w *= 0.6
            elif is_sideways and category == "momentum":
                w *= 0.6

            # Signal quality: stronger signals (farther from 0) get more weight
            quality = abs(value)
            w *= (0.5 + 0.5 * quality)  # range [0.5, 1.0] multiplier

            # Recency weighting: signals later in the list are from more
            # "reactive" indicators. Small bonus (up to 15%).
            recency_factor = 1.0 + 0.15 * (idx / max(len(raw_signals) - 1, 1))
            w *= recency_factor

            # Trend filter: suppress counter-trend signals when ADX > 35
            if adx is not None and adx > 35 and is_trending:
                trend_dir = self._infer_trend_direction(features)
                if trend_dir != 0:
                    # If signal opposes the trend, strongly dampen
                    if (value > 0 and trend_dir < 0) or (value < 0 and trend_dir > 0):
                        w *= 0.25  # nearly muted

            weighted_signals.append((name, value, w))
            components[name] = round(value, 4)

        # ------------------------------------------------------------------
        # 3. Weighted average
        # ------------------------------------------------------------------
        total_weight = sum(w for _, _, w in weighted_signals)
        if total_weight == 0:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        raw_score = sum(v * w for _, v, w in weighted_signals) / total_weight

        # ------------------------------------------------------------------
        # 4. Signal agreement bonus
        # ------------------------------------------------------------------
        n_signals = len(weighted_signals)
        if n_signals >= 4:
            n_bullish = sum(1 for _, v, _ in weighted_signals if v > 0.1)
            n_bearish = sum(1 for _, v, _ in weighted_signals if v < -0.1)
            max_agree = max(n_bullish, n_bearish)
            agreement_ratio = max_agree / n_signals

            if agreement_ratio >= 0.8:
                # Strong consensus: boost score magnitude by 20%
                raw_score *= 1.20
                components["agreement_bonus"] = round(agreement_ratio, 3)
            elif agreement_ratio >= 0.6:
                raw_score *= 1.08
                components["agreement_bonus"] = round(agreement_ratio, 3)

        # ------------------------------------------------------------------
        # 5. Volatility-adjusted dampening
        # ------------------------------------------------------------------
        vol_dampen = 1.0
        if regime.volatility == "high":
            vol_dampen = 0.70
        elif regime.volatility == "low":
            vol_dampen = 1.10  # slightly boost in calm markets

        raw_score *= vol_dampen
        components["vol_dampening"] = round(vol_dampen, 3)

        # Clamp to [-1, 1]
        score = max(-1.0, min(1.0, raw_score))

        # ------------------------------------------------------------------
        # 6. Confidence estimation
        # ------------------------------------------------------------------
        # Base: proportion of available signals and their average strength
        avg_strength = sum(abs(v) for _, v, _ in weighted_signals) / n_signals
        coverage = min(n_signals / 7, 1.0)  # 7 possible signals
        confidence = avg_strength * 0.5 + coverage * 0.3 + regime.confidence * 0.2
        confidence = min(confidence, 1.0)

        components["regime_confidence"] = regime.confidence
        components["n_signals"] = n_signals

        return FormulaResult(
            score=round(score, 5),
            confidence=round(confidence, 4),
            components=components,
            formula_name=self.name,
            regime_label=regime.label,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_trend_direction(features: dict) -> int:
        """Infer dominant trend direction: +1 bullish, -1 bearish, 0 unclear."""
        votes = 0

        ema_9 = features.get("ema_9")
        ema_21 = features.get("ema_21")
        ema_50 = features.get("ema_50")
        close = features.get("close")

        if ema_9 is not None and ema_21 is not None:
            votes += 1 if ema_9 > ema_21 else -1

        if ema_21 is not None and ema_50 is not None:
            votes += 1 if ema_21 > ema_50 else -1

        if close is not None and ema_50 is not None:
            votes += 1 if close > ema_50 else -1

        macd = features.get("macd")
        macd_sig = features.get("macd_signal")
        if macd is not None and macd_sig is not None:
            votes += 1 if macd > macd_sig else -1

        if votes >= 2:
            return 1
        elif votes <= -2:
            return -1
        return 0


formula_registry.register(CompositeAdaptiveFormula())
