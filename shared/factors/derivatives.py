"""Derivatives factors using external data service features."""
from __future__ import annotations
import math
from shared.factors.base import Factor


class FundingRateSignal(Factor):
    """Funding rate as a contrarian signal: high positive -> bearish, negative -> bullish."""

    def __init__(self):
        super().__init__(
            name="funding_rate_signal",
            category="derivatives",
            description="High positive funding -> bearish (crowded long), negative -> bullish",
        )

    def compute(self, features: dict) -> float:
        funding = self._safe_get(features, "funding_rate")
        score = self._safe_get(features, "funding_rate_score")
        # Prefer pre-computed score if available
        if score != 0.0:
            return max(-1.0, min(1.0, score))
        if funding == 0.0:
            return 0.0
        # Contrarian: negate funding rate
        # Typical funding: -0.01% to +0.05%, extreme: > 0.1%
        return self._tanh_norm(-funding, 0.0005)


class OpenInterestTrend(Factor):
    """Open interest trend combined with price direction."""

    def __init__(self):
        super().__init__(
            name="open_interest_trend",
            category="derivatives",
            description="Rising OI + rising price -> bullish, rising OI + falling price -> bearish",
        )

    def compute(self, features: dict) -> float:
        oi_score = self._safe_get(features, "open_interest_score")
        if oi_score != 0.0:
            return max(-1.0, min(1.0, oi_score))
        # Without pre-computed score, use price momentum as proxy
        close = self._safe_get(features, "close")
        ema9 = self._safe_get(features, "ema_9")
        if close == 0.0 or ema9 == 0.0:
            return 0.0
        # Price direction as a weak proxy
        atr = self._safe_get(features, "atr_14")
        if atr <= 0:
            return 0.0
        return self._tanh_norm(close - ema9, atr * 2.0)


class LongShortRatio(Factor):
    """Long/short ratio contrarian signal."""

    def __init__(self):
        super().__init__(
            name="long_short_ratio",
            category="derivatives",
            description="Extreme long bias -> contrarian bearish, extreme short bias -> bullish",
        )

    def compute(self, features: dict) -> float:
        score = self._safe_get(features, "long_short_score")
        if score != 0.0:
            return max(-1.0, min(1.0, score))
        ratio = self._safe_get(features, "long_short_ratio")
        if ratio == 0.0:
            return 0.0
        # Neutral ratio is ~1.0; >1.5 is crowded long, <0.67 is crowded short
        # Contrarian: high ratio -> bearish, low ratio -> bullish
        return self._tanh_norm(-(ratio - 1.0), 0.5)


class TakerBuySell(Factor):
    """Taker buy/sell ratio signal."""

    def __init__(self):
        super().__init__(
            name="taker_buy_sell",
            category="derivatives",
            description="Taker buy/sell ratio: >1 bullish, <1 bearish",
        )

    def compute(self, features: dict) -> float:
        score = self._safe_get(features, "taker_buy_sell_score")
        if score != 0.0:
            return max(-1.0, min(1.0, score))
        ratio = self._safe_get(features, "taker_buy_sell_ratio")
        if ratio == 0.0:
            return 0.0
        # >1 means more taker buys -> bullish, <1 -> bearish
        return self._tanh_norm(ratio - 1.0, 0.3)


class DerivativesSentiment(Factor):
    """Pre-computed composite derivatives sentiment."""

    def __init__(self):
        super().__init__(
            name="derivatives_sentiment",
            category="derivatives",
            description="Pre-computed composite from external data",
        )

    def compute(self, features: dict) -> float:
        score = self._safe_get(features, "derivatives_sentiment")
        return max(-1.0, min(1.0, score))


class FundingRateExtreme(Factor):
    """Contrarian signal at extreme funding rates."""

    def __init__(self):
        super().__init__(
            name="funding_rate_extreme",
            category="derivatives",
            description="Very high/low funding rates -> contrarian signal",
        )

    def compute(self, features: dict) -> float:
        funding = self._safe_get(features, "funding_rate")
        if funding == 0.0:
            return 0.0
        # Only fire at extremes: abs(funding) > 0.0003 (0.03%)
        abs_funding = abs(funding)
        if abs_funding < 0.0003:
            return 0.0
        # Scale the extreme portion
        extreme_portion = abs_funding - 0.0003
        # Contrarian direction
        direction = -1.0 if funding > 0 else 1.0
        return direction * min(1.0, self._tanh_norm(extreme_portion, 0.0005))


DERIVATIVES_FACTORS = [
    FundingRateSignal(),
    OpenInterestTrend(),
    LongShortRatio(),
    TakerBuySell(),
    DerivativesSentiment(),
    FundingRateExtreme(),
]
