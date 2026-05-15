"""Volatility breakout formula — detects and scores breakouts from consolidation.

Uses adaptive squeeze detection (Bollinger inside Keltner), volume/momentum
confirmation, consolidation duration tracking, and ATR-normalized scoring.
"""
from __future__ import annotations

import math
from collections import deque

from shared.formulas.base import BaseFormula, FormulaResult
from shared.formulas.registry import formula_registry


class VolatilityBreakoutFormula(BaseFormula):
    name = "volatility_breakout"
    description = (
        "Adaptive Bollinger/Keltner squeeze breakout with volume and momentum confirmation"
    )
    best_regime = "breakout"
    required_indicators = [
        "close",
        "sma_20",
        "bb_upper",
        "bb_lower",
        "atr_14",
        "ema_20",
        "volume",
        "rsi_14",
        "obv",
        "adx_14",
    ]

    # --- tunables ---
    BANDWIDTH_WINDOW = 100  # rolling window for adaptive percentile
    SQUEEZE_PERCENTILE = 0.20  # bottom 20% of bandwidth = squeeze
    VOLUME_WINDOW = 20  # bars for average volume
    VOLUME_BOOST = 1.30  # multiplier when volume confirms
    KELTNER_MULT = 1.5  # Keltner channel = EMA +/- mult * ATR
    MAX_CONSOLIDATION_BARS = 50  # cap for duration factor

    def __init__(self) -> None:
        super().__init__()
        self._bw_history: deque[float] = deque(maxlen=self.BANDWIDTH_WINDOW)
        self._vol_history: deque[float] = deque(maxlen=self.VOLUME_WINDOW)
        self._squeeze_bars: int = 0  # consecutive bars in squeeze

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(data: deque, pct: float) -> float:
        """Simple percentile without numpy — O(n log n) but n is small."""
        if not data:
            return 0.0
        s = sorted(data)
        idx = pct * (len(s) - 1)
        lo = int(math.floor(idx))
        hi = min(lo + 1, len(s) - 1)
        frac = idx - lo
        return s[lo] * (1 - frac) + s[hi] * frac

    def _detect_keltner_squeeze(
        self,
        bb_upper: float,
        bb_lower: float,
        ema_20: float,
        atr: float,
    ) -> bool:
        """True squeeze: Bollinger Bands are inside the Keltner Channel."""
        kc_upper = ema_20 + self.KELTNER_MULT * atr
        kc_lower = ema_20 - self.KELTNER_MULT * atr
        return bb_upper < kc_upper and bb_lower > kc_lower

    def _adaptive_squeeze(self, bb_width: float) -> bool:
        """Bandwidth is in the bottom SQUEEZE_PERCENTILE of recent history."""
        if len(self._bw_history) < 20:
            # not enough history — fall back to static threshold
            return bb_width < 0.04
        threshold = self._percentile(self._bw_history, self.SQUEEZE_PERCENTILE)
        return bb_width <= threshold

    def _volume_confirms(self, volume: float | None) -> float:
        """Return multiplier: VOLUME_BOOST if above average, else 1.0."""
        if volume is None or len(self._vol_history) < 5:
            return 1.0
        avg_vol = sum(self._vol_history) / len(self._vol_history)
        if avg_vol <= 0:
            return 1.0
        return self.VOLUME_BOOST if volume > avg_vol else 1.0

    @staticmethod
    def _momentum_confirms(rsi: float | None, direction: float) -> float:
        """Return multiplier based on RSI alignment with breakout direction.

        Upside breakout + RSI > 50 = confirmed (1.15x).
        Downside breakout + RSI < 50 = confirmed (1.15x).
        Misaligned = dampened (0.75x).
        """
        if rsi is None:
            return 1.0
        if direction > 0:
            return 1.15 if rsi > 50 else 0.75
        if direction < 0:
            return 1.15 if rsi < 50 else 0.75
        return 1.0

    def _consolidation_factor(self) -> float:
        """Longer squeeze -> stronger expected breakout. Range [1.0, 2.0]."""
        capped = min(self._squeeze_bars, self.MAX_CONSOLIDATION_BARS)
        # logarithmic scaling so early bars matter most
        return 1.0 + math.log1p(capped) / math.log1p(self.MAX_CONSOLIDATION_BARS)

    # ------------------------------------------------------------------
    # main
    # ------------------------------------------------------------------

    def compute(self, features: dict) -> FormulaResult:
        close = features.get("close")
        sma_20 = features.get("sma_20")
        bb_upper = features.get("bb_upper")
        bb_lower = features.get("bb_lower")
        atr = features.get("atr_14") or 1.0
        volume = features.get("volume")
        rsi = features.get("rsi_14")
        adx = features.get("adx_14")

        if close is None or sma_20 is None or bb_upper is None or bb_lower is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        # --- Bollinger bandwidth ---
        bb_width = (bb_upper - bb_lower) / sma_20 if sma_20 > 0 else 0.0
        self._bw_history.append(bb_width)

        # --- volume tracking ---
        if volume is not None:
            self._vol_history.append(volume)

        # --- Keltner squeeze detection ---
        # Use sma_20 as proxy for ema_20 if dedicated ema_20 not in features
        ema_20 = features.get("ema_20") or sma_20
        keltner_squeeze = self._detect_keltner_squeeze(bb_upper, bb_lower, ema_20, atr)
        adaptive_squeeze = self._adaptive_squeeze(bb_width)
        is_squeeze = keltner_squeeze or adaptive_squeeze

        # --- consolidation tracking ---
        if is_squeeze:
            self._squeeze_bars += 1
        else:
            # Only reset after a breakout actually fires; keep counting otherwise
            pass

        # --- breakout direction (ATR-normalized distance from band) ---
        if close > bb_upper:
            direction = 1.0
            raw_distance = (close - bb_upper) / atr
        elif close < bb_lower:
            direction = -1.0
            raw_distance = (bb_lower - close) / atr
        else:
            direction = 0.0
            raw_distance = 0.0

        # --- no breakout: return low signal ---
        if direction == 0.0:
            # Still in consolidation — provide a "coiling" hint via confidence
            coil_conf = 0.1 * self._consolidation_factor() if is_squeeze else 0.0
            return FormulaResult(
                score=0.0,
                confidence=min(coil_conf, 0.4),
                components={
                    "bb_width": round(bb_width, 6),
                    "is_squeeze": is_squeeze,
                    "keltner_squeeze": keltner_squeeze,
                    "squeeze_bars": self._squeeze_bars,
                    "direction": 0.0,
                },
                formula_name=self.name,
            )

        # --- confirmation multipliers ---
        vol_mult = self._volume_confirms(volume)
        mom_mult = self._momentum_confirms(rsi, direction)
        consol_mult = self._consolidation_factor()

        # --- ADX trend-strength factor ---
        adx_factor = 1.0
        if adx is not None:
            # ADX > 25 means strong trend developing — boost
            adx_factor = min(max(adx / 25.0, 0.6), 1.4)

        # --- composite score ---
        # ATR-normalized distance through tanh for smooth [-1, 1] bounding
        base_signal = math.tanh(raw_distance * 0.8)
        score = direction * base_signal * vol_mult * mom_mult * consol_mult * adx_factor
        score = max(-1.0, min(1.0, score))

        # --- confidence ---
        # Higher with: large ATR-distance, volume confirmation, long squeeze
        dist_conf = min(raw_distance / 2.0, 1.0)
        vol_conf = 0.9 if vol_mult > 1.0 else 0.6
        conf = dist_conf * vol_conf * min(consol_mult, 1.5) / 1.5
        confidence = max(0.0, min(1.0, conf))

        # --- reset squeeze counter after breakout fires ---
        self._squeeze_bars = 0

        return FormulaResult(
            score=score,
            confidence=confidence,
            components={
                "bb_width": round(bb_width, 6),
                "is_squeeze": is_squeeze,
                "keltner_squeeze": keltner_squeeze,
                "squeeze_bars_at_breakout": int(consol_mult > 1.0),
                "consolidation_mult": round(consol_mult, 3),
                "direction": direction,
                "atr_distance": round(raw_distance, 4),
                "volume_mult": round(vol_mult, 2),
                "momentum_mult": round(mom_mult, 2),
                "adx_factor": round(adx_factor, 3),
            },
            formula_name=self.name,
        )


formula_registry.register(VolatilityBreakoutFormula())
