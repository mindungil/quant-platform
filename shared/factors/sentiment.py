"""Sentiment factors using external data sources."""
from __future__ import annotations
import math
from shared.factors.base import Factor


class FearGreed(Factor):
    """Fear & Greed index as a contrarian signal."""

    def __init__(self):
        super().__init__(
            name="fear_greed",
            category="sentiment",
            description="Fear & Greed: extreme fear -> buy, extreme greed -> sell",
        )

    def compute(self, features: dict) -> float:
        fg = self._safe_get(features, "fear_greed_index")
        if fg == 0.0:
            return 0.0
        # FG index: 0-100, 50 = neutral
        # Contrarian: high greed -> sell (-1), extreme fear -> buy (+1)
        return max(-1.0, min(1.0, -(fg - 50.0) / 50.0))


class BTCDominance(Factor):
    """BTC dominance trend signal."""

    def __init__(self):
        super().__init__(
            name="btc_dominance",
            category="sentiment",
            description="BTC dominance trend signal",
        )

    def compute(self, features: dict) -> float:
        dominance = self._safe_get(features, "btc_dominance")
        if dominance == 0.0:
            return 0.0
        # BTC dominance typically 40-70%
        # High dominance (>55%) -> risk-off (bearish for alts, slightly bullish BTC)
        # Low dominance (<45%) -> risk-on (bullish for alts)
        # Normalize around 50%
        return self._tanh_norm(dominance - 50.0, 15.0)


class NewsSentiment(Factor):
    """Pre-computed news sentiment from external data."""

    def __init__(self):
        super().__init__(
            name="news_sentiment",
            category="sentiment",
            description="Pre-computed from external data",
        )

    def compute(self, features: dict) -> float:
        score = self._safe_get(features, "news_sentiment")
        return max(-1.0, min(1.0, score))


class OnchainScore(Factor):
    """Pre-computed on-chain analysis score."""

    def __init__(self):
        super().__init__(
            name="onchain_score",
            category="sentiment",
            description="Pre-computed from external data",
        )

    def compute(self, features: dict) -> float:
        score = self._safe_get(features, "onchain_score")
        return max(-1.0, min(1.0, score))


class MacroRisk(Factor):
    """Macro risk score: high risk -> bearish."""

    def __init__(self):
        super().__init__(
            name="macro_risk",
            category="sentiment",
            description="Macro risk score (inverse: high risk = bearish)",
        )

    def compute(self, features: dict) -> float:
        risk = self._safe_get(features, "macro_risk_score")
        if risk == 0.0:
            return 0.0
        # Risk score assumed 0-1 or -1 to 1
        # High risk -> bearish (negative signal)
        # If score is 0-1: 0.5 = neutral
        if 0.0 <= risk <= 1.0:
            return max(-1.0, min(1.0, -(risk - 0.5) * 2.0))
        # If already in [-1, 1] range
        return max(-1.0, min(1.0, -risk))


SENTIMENT_FACTORS = [
    FearGreed(),
    BTCDominance(),
    NewsSentiment(),
    OnchainScore(),
    MacroRisk(),
]
