"""Market regime detection from technical indicators.

Classifies market state into trend/volatility/momentum dimensions,
producing a composite label that agents use to select appropriate formulas.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class MarketRegime:
    """Current market regime classification."""
    trend_strength: str = "unknown"    # trending | sideways | unknown
    volatility: str = "normal"         # low | normal | high
    momentum: str = "neutral"          # bullish | bearish | neutral
    label: str = "unknown"             # composite: e.g. "trending_high_bullish"
    confidence: float = 0.0            # [0, 1] overall classification confidence

    def to_dict(self) -> dict:
        return {
            "trend_strength": self.trend_strength,
            "volatility": self.volatility,
            "momentum": self.momentum,
            "label": self.label,
            "confidence": self.confidence,
        }


def _classify_trend(adx: float | None) -> tuple[str, float]:
    """Classify trend strength from ADX."""
    if adx is None:
        return "unknown", 0.0
    if adx >= 25:
        confidence = min((adx - 25) / 25, 1.0)
        return "trending", confidence
    else:
        confidence = min((25 - adx) / 15, 1.0)
        return "sideways", confidence


def _classify_volatility(
    atr: float | None,
    close: float | None,
    bb_upper: float | None,
    bb_lower: float | None,
) -> tuple[str, float]:
    """Classify volatility from ATR and Bollinger bandwidth."""
    if atr is None or close is None or close <= 0:
        return "normal", 0.0

    # ATR as percentage of price
    atr_pct = atr / close

    # Bollinger bandwidth if available
    bb_width = None
    if bb_upper is not None and bb_lower is not None and close > 0:
        bb_width = (bb_upper - bb_lower) / close

    # Thresholds (crypto-typical: ATR ~1-5% of price)
    if atr_pct > 0.03 or (bb_width is not None and bb_width > 0.08):
        return "high", min(atr_pct / 0.05, 1.0)
    elif atr_pct < 0.01 or (bb_width is not None and bb_width < 0.03):
        return "low", min(0.02 / max(atr_pct, 0.001), 1.0)
    else:
        return "normal", 0.5


def _classify_momentum(rsi: float | None, macd: float | None, macd_signal: float | None) -> tuple[str, float]:
    """Classify momentum from RSI and MACD."""
    signals = []

    if rsi is not None:
        if rsi > 60:
            signals.append(("bullish", min((rsi - 50) / 30, 1.0)))
        elif rsi < 40:
            signals.append(("bearish", min((50 - rsi) / 30, 1.0)))
        else:
            signals.append(("neutral", 0.3))

    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            signals.append(("bullish", 0.6))
        elif macd < macd_signal:
            signals.append(("bearish", 0.6))
        else:
            signals.append(("neutral", 0.3))

    if not signals:
        return "neutral", 0.0

    # Majority vote
    votes = {"bullish": 0, "bearish": 0, "neutral": 0}
    total_conf = 0.0
    for label, conf in signals:
        votes[label] += 1
        total_conf += conf

    winner = max(votes, key=votes.get)
    confidence = total_conf / len(signals)
    return winner, confidence


def detect_regime(features: dict) -> MarketRegime:
    """Detect market regime from a features dictionary.

    Args:
        features: Dict with keys like 'adx_14', 'atr_14', 'close', 'rsi_14',
                  'macd', 'macd_signal', 'bb_upper', 'bb_lower', etc.

    Returns:
        MarketRegime with trend, volatility, momentum classifications.
    """
    trend, trend_conf = _classify_trend(features.get("adx_14"))
    vol, vol_conf = _classify_volatility(
        features.get("atr_14"),
        features.get("close"),
        features.get("bb_upper"),
        features.get("bb_lower"),
    )
    momentum, mom_conf = _classify_momentum(
        features.get("rsi_14"),
        features.get("macd"),
        features.get("macd_signal"),
    )

    label = f"{trend}_{vol}_{momentum}"
    overall_confidence = (trend_conf + vol_conf + mom_conf) / 3

    return MarketRegime(
        trend_strength=trend,
        volatility=vol,
        momentum=momentum,
        label=label,
        confidence=round(overall_confidence, 3),
    )


def suggest_formula_type(regime: MarketRegime) -> str:
    """Suggest which formula type is best for this regime.

    Returns one of: trending, sideways, reversal, breakout, any
    """
    if regime.trend_strength == "trending":
        return "trending"
    elif regime.trend_strength == "sideways":
        if regime.volatility == "low":
            return "breakout"  # low vol sideways = potential breakout
        return "sideways"
    elif regime.momentum in ("bullish", "bearish") and regime.volatility == "high":
        return "breakout"

    return "any"
