"""Market regime detection from technical indicators.

Classifies market state into trend/volatility/momentum dimensions,
producing a composite label that agents use to select appropriate formulas.

Features:
- Adaptive thresholds via z-score normalization against recent ranges
- Volatility clustering detection (expanding vs contracting ATR)
- Weighted confidence scoring (not majority vote)
- ADX direction awareness (rising vs falling trend strength)
- Regime persistence tracking (duration in bars)
- Micro-regime detection (breakout_imminent, exhaustion, etc.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Rolling statistics helper — lightweight z-score tracker
# ---------------------------------------------------------------------------

class _RollingStats:
    """Maintain running mean/std over a fixed window for z-score computation."""

    def __init__(self, window: int = 50):
        self._window = window
        self._values: list[float] = []

    def update(self, value: float) -> None:
        self._values.append(value)
        if len(self._values) > self._window:
            self._values.pop(0)

    def z_score(self, value: float) -> float:
        """Return how many std-devs *value* is from the rolling mean."""
        if len(self._values) < 5:
            return 0.0
        mean = sum(self._values) / len(self._values)
        var = sum((v - mean) ** 2 for v in self._values) / len(self._values)
        std = math.sqrt(var) if var > 0 else 1e-9
        return (value - mean) / std

    @property
    def mean(self) -> float:
        return sum(self._values) / len(self._values) if self._values else 0.0

    @property
    def count(self) -> int:
        return len(self._values)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

RegimeLabel = Literal[
    "trending", "sideways", "volatile_trending", "volatile_sideways",
    "breakout_imminent", "exhaustion", "unknown",
]

VolatilityTrend = Literal["expanding", "contracting", "stable", "unknown"]


@dataclass
class MarketRegime:
    """Current market regime classification."""

    trend_strength: str = "unknown"       # trending | sideways | unknown
    volatility: str = "normal"            # low | normal | high
    momentum: str = "neutral"             # bullish | bearish | neutral
    label: str = "unknown"                # composite regime label
    confidence: float = 0.0               # [0, 1] overall classification confidence

    # --- new fields ---
    duration_bars: int = 0                # how many bars the current regime has persisted
    volatility_trend: str = "unknown"     # expanding | contracting | stable | unknown
    regime_strength: float = 0.0          # [0, 1] strength/conviction of the regime
    micro_regime: str = ""                # optional sub-classification

    def to_dict(self) -> dict:
        return {
            "trend_strength": self.trend_strength,
            "volatility": self.volatility,
            "momentum": self.momentum,
            "label": self.label,
            "confidence": self.confidence,
            "duration_bars": self.duration_bars,
            "volatility_trend": self.volatility_trend,
            "regime_strength": self.regime_strength,
            "micro_regime": self.micro_regime,
        }


# ---------------------------------------------------------------------------
# Stateful detector — keeps history for adaptive thresholds
# ---------------------------------------------------------------------------

class RegimeDetector:
    """Stateful regime detector with adaptive thresholds and persistence tracking.

    Usage:
        detector = RegimeDetector()
        regime = detector.update(features)   # call once per bar
    """

    def __init__(self, lookback: int = 50):
        self._adx_stats = _RollingStats(lookback)
        self._atr_pct_stats = _RollingStats(lookback)
        self._rsi_stats = _RollingStats(lookback)
        self._bb_width_stats = _RollingStats(lookback)
        self._macd_hist_stats = _RollingStats(lookback)

        # Persistence tracking
        self._prev_label: str = "unknown"
        self._duration_bars: int = 0

        # ADX direction tracking (last N ADX values)
        self._adx_history: list[float] = []
        self._adx_history_len = 5

        # ATR history for volatility trend
        self._atr_history: list[float] = []
        self._atr_history_len = 10

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, features: dict) -> MarketRegime:
        """Ingest a new bar's features and return the current regime."""
        self._feed_stats(features)

        trend, trend_conf = self._classify_trend(features)
        vol, vol_conf = self._classify_volatility(features)
        momentum, mom_conf = self._classify_momentum(features)
        vol_trend = self._detect_volatility_trend()
        micro = self._detect_micro_regime(features, trend, vol, vol_trend, momentum)

        # Weighted overall confidence (trend matters most)
        overall_confidence = (
            trend_conf * 0.40
            + vol_conf * 0.30
            + mom_conf * 0.30
        )

        # Composite label
        label = self._build_label(trend, vol, momentum, micro)

        # Regime strength: geometric mean of individual confidences (avoids
        # a single weak dimension inflating the result)
        regime_strength = (
            max(trend_conf, 0.01) * max(vol_conf, 0.01) * max(mom_conf, 0.01)
        ) ** (1 / 3)

        # Persistence
        if label == self._prev_label:
            self._duration_bars += 1
        else:
            self._duration_bars = 1
            self._prev_label = label

        return MarketRegime(
            trend_strength=trend,
            volatility=vol,
            momentum=momentum,
            label=label,
            confidence=round(overall_confidence, 4),
            duration_bars=self._duration_bars,
            volatility_trend=vol_trend,
            regime_strength=round(regime_strength, 4),
            micro_regime=micro,
        )

    # ------------------------------------------------------------------
    # Stats ingestion
    # ------------------------------------------------------------------

    def _feed_stats(self, features: dict) -> None:
        close = features.get("close")
        adx = features.get("adx_14")
        atr = features.get("atr_14")
        rsi = features.get("rsi_14")
        macd = features.get("macd")
        macd_signal = features.get("macd_signal")
        bb_upper = features.get("bb_upper")
        bb_lower = features.get("bb_lower")

        if adx is not None:
            self._adx_stats.update(adx)
            self._adx_history.append(adx)
            if len(self._adx_history) > self._adx_history_len:
                self._adx_history.pop(0)

        if atr is not None and close and close > 0:
            atr_pct = atr / close
            self._atr_pct_stats.update(atr_pct)
            self._atr_history.append(atr_pct)
            if len(self._atr_history) > self._atr_history_len:
                self._atr_history.pop(0)

        if rsi is not None:
            self._rsi_stats.update(rsi)

        if macd is not None and macd_signal is not None:
            self._macd_hist_stats.update(macd - macd_signal)

        if bb_upper is not None and bb_lower is not None and close and close > 0:
            self._bb_width_stats.update((bb_upper - bb_lower) / close)

    # ------------------------------------------------------------------
    # Trend classification (ADX-based, direction-aware)
    # ------------------------------------------------------------------

    def _classify_trend(self, features: dict) -> tuple[str, float]:
        adx = features.get("adx_14")
        if adx is None:
            return "unknown", 0.0

        z = self._adx_stats.z_score(adx)
        adx_rising = self._is_adx_rising()

        # Adaptive: trending if ADX is significantly above its recent mean
        # Also factor in whether ADX is rising (strengthening trend)
        if z > 0.5 or (adx >= 25 and adx_rising):
            # Confidence scales with how extreme the z-score is
            raw_conf = min(abs(z) / 2.0, 1.0)
            # Boost confidence if ADX is also rising
            if adx_rising:
                raw_conf = min(raw_conf + 0.15, 1.0)
            return "trending", raw_conf
        elif z < -0.5 or adx < 20:
            raw_conf = min(abs(z) / 2.0, 1.0)
            if not adx_rising:
                raw_conf = min(raw_conf + 0.1, 1.0)
            return "sideways", raw_conf
        else:
            # Ambiguous zone — lean based on ADX direction
            if adx_rising:
                return "trending", 0.35
            return "sideways", 0.35

    def _is_adx_rising(self) -> bool:
        if len(self._adx_history) < 3:
            return False
        recent = self._adx_history[-3:]
        return recent[-1] > recent[0]

    # ------------------------------------------------------------------
    # Volatility classification (ATR + BB, z-score adaptive)
    # ------------------------------------------------------------------

    def _classify_volatility(self, features: dict) -> tuple[str, float]:
        close = features.get("close")
        atr = features.get("atr_14")
        bb_upper = features.get("bb_upper")
        bb_lower = features.get("bb_lower")

        if atr is None or not close or close <= 0:
            return "normal", 0.0

        atr_pct = atr / close
        atr_z = self._atr_pct_stats.z_score(atr_pct)

        # BB width z-score as secondary signal
        bb_z = 0.0
        if bb_upper is not None and bb_lower is not None:
            bb_width = (bb_upper - bb_lower) / close
            bb_z = self._bb_width_stats.z_score(bb_width)

        # Blend ATR z and BB z (ATR primary)
        blended_z = atr_z * 0.65 + bb_z * 0.35 if bb_z != 0.0 else atr_z

        if blended_z > 1.0:
            return "high", min(blended_z / 2.5, 1.0)
        elif blended_z < -1.0:
            return "low", min(abs(blended_z) / 2.5, 1.0)
        else:
            return "normal", 0.4

    # ------------------------------------------------------------------
    # Momentum classification (weighted scoring, not majority vote)
    # ------------------------------------------------------------------

    def _classify_momentum(self, features: dict) -> tuple[str, float]:
        rsi = features.get("rsi_14")
        macd = features.get("macd")
        macd_signal = features.get("macd_signal")
        stoch_k = features.get("stochastic_k")
        stoch_d = features.get("stochastic_d")
        ema_9 = features.get("ema_9")
        ema_21 = features.get("ema_21")
        close = features.get("close")

        # Accumulate weighted directional score: positive = bullish, negative = bearish
        total_score = 0.0
        total_weight = 0.0

        # RSI contribution (weight=3) — use z-score for adaptive threshold
        if rsi is not None:
            rsi_z = self._rsi_stats.z_score(rsi)
            # Also use absolute level for extreme readings
            level_bias = (rsi - 50) / 50  # [-1, 1]
            rsi_signal = rsi_z * 0.5 + level_bias * 0.5
            total_score += rsi_signal * 3.0
            total_weight += 3.0

        # MACD histogram contribution (weight=3)
        if macd is not None and macd_signal is not None:
            hist = macd - macd_signal
            hist_z = self._macd_hist_stats.z_score(hist)
            total_score += math.tanh(hist_z) * 3.0
            total_weight += 3.0

        # Stochastic contribution (weight=2)
        if stoch_k is not None and stoch_d is not None:
            stoch_signal = (stoch_k - 50) / 50
            # Cross bonus
            if stoch_k > stoch_d:
                stoch_signal += 0.15
            elif stoch_k < stoch_d:
                stoch_signal -= 0.15
            total_score += max(-1.0, min(1.0, stoch_signal)) * 2.0
            total_weight += 2.0

        # EMA cross contribution (weight=2)
        if ema_9 is not None and ema_21 is not None and close and close > 0:
            ema_diff = (ema_9 - ema_21) / close
            total_score += math.tanh(ema_diff * 100) * 2.0  # scale for tanh
            total_weight += 2.0

        if total_weight == 0:
            return "neutral", 0.0

        normalized = total_score / total_weight  # [-1, 1] range approx

        if normalized > 0.2:
            return "bullish", min(abs(normalized), 1.0)
        elif normalized < -0.2:
            return "bearish", min(abs(normalized), 1.0)
        else:
            return "neutral", 0.3

    # ------------------------------------------------------------------
    # Volatility trend (expanding / contracting / stable)
    # ------------------------------------------------------------------

    def _detect_volatility_trend(self) -> str:
        if len(self._atr_history) < 5:
            return "unknown"

        recent = self._atr_history[-5:]
        # Simple linear regression slope sign
        n = len(recent)
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(recent))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            return "stable"
        slope = numerator / denominator

        # Normalize slope relative to mean ATR
        if y_mean > 0:
            rel_slope = slope / y_mean
        else:
            return "stable"

        if rel_slope > 0.02:
            return "expanding"
        elif rel_slope < -0.02:
            return "contracting"
        return "stable"

    # ------------------------------------------------------------------
    # Micro-regime detection
    # ------------------------------------------------------------------

    def _detect_micro_regime(
        self,
        features: dict,
        trend: str,
        vol: str,
        vol_trend: str,
        momentum: str,
    ) -> str:
        adx = features.get("adx_14")
        rsi = features.get("rsi_14")

        # Breakout imminent: volatility contracting + momentum building (ADX rising)
        if vol_trend == "contracting" and self._is_adx_rising():
            return "breakout_imminent"

        # Exhaustion: strong trend but momentum fading (overbought/oversold RSI
        # diverging from trend direction)
        if trend == "trending" and adx is not None and adx > 30 and rsi is not None:
            if momentum == "bullish" and rsi > 75:
                return "exhaustion"
            if momentum == "bearish" and rsi < 25:
                return "exhaustion"

        # Compression: very low vol + sideways, coiled spring
        if vol == "low" and trend == "sideways":
            return "compression"

        # Momentum surge: high vol + strong momentum shift
        if vol == "high" and momentum in ("bullish", "bearish") and vol_trend == "expanding":
            return "momentum_surge"

        return ""

    # ------------------------------------------------------------------
    # Label construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_label(trend: str, vol: str, momentum: str, micro: str) -> str:
        if micro:
            return micro
        # Use composite labels that are more actionable
        if trend == "trending" and vol == "high":
            return "volatile_trending"
        if trend == "sideways" and vol == "low":
            return "compression"
        return f"{trend}_{vol}_{momentum}"


# ---------------------------------------------------------------------------
# Module-level convenience (stateless, for backward compat)
# ---------------------------------------------------------------------------

# Per-asset detector instances to prevent cross-asset state contamination.
_detectors: dict[str, RegimeDetector] = {}


def detect_regime(features: dict, asset: str | None = None) -> MarketRegime:
    """Detect market regime from a features dictionary.

    Stateful per asset: each asset maintains independent rolling statistics
    to prevent cross-contamination between different assets/timeframes.

    The asset key is resolved in order: explicit argument > features["asset"] > "default".

    Args:
        features: Dict with keys like 'adx_14', 'atr_14', 'close', 'rsi_14',
                  'macd', 'macd_signal', 'bb_upper', 'bb_lower', etc.
                  May also contain 'asset' key for automatic isolation.
        asset: Asset identifier for per-asset state isolation (e.g. 'BTCUSDT').

    Returns:
        MarketRegime with trend, volatility, momentum, and micro-regime info.
    """
    key = asset or features.get("asset") or "default"
    if key not in _detectors:
        _detectors[key] = RegimeDetector()
    return _detectors[key].update(features)


def suggest_formula_type(regime: MarketRegime) -> str:
    """Suggest which formula type is best for this regime.

    Returns a nuanced recommendation based on regime dimensions and micro-state.

    Possible returns:
        trending, sideways, reversal, breakout, momentum_follow,
        volatility_fade, conservative, any
    """
    micro = regime.micro_regime

    # Micro-regime overrides take priority
    if micro == "breakout_imminent":
        return "breakout"
    if micro == "exhaustion":
        return "reversal"
    if micro == "compression":
        return "breakout"  # coiled spring
    if micro == "momentum_surge":
        return "momentum_follow"

    # Standard regime-based selection
    if regime.trend_strength == "trending":
        if regime.volatility == "high":
            # Trending but volatile — need trailing stops / momentum follow
            return "momentum_follow"
        if regime.momentum == "neutral":
            # Trend present but momentum stalling
            return "conservative"
        return "trending"

    if regime.trend_strength == "sideways":
        if regime.volatility == "low":
            return "breakout"
        if regime.volatility == "high":
            return "volatility_fade"
        return "sideways"

    # Unknown trend
    if regime.momentum in ("bullish", "bearish") and regime.confidence > 0.5:
        return "momentum_follow"

    return "any"
