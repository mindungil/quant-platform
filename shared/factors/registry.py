"""Factor registry -- single point of access for all factors."""
from __future__ import annotations

from shared.factors.base import Factor
from shared.factors.technical import TECHNICAL_FACTORS
from shared.factors.momentum import MOMENTUM_FACTORS
from shared.factors.mean_reversion import MEAN_REVERSION_FACTORS
from shared.factors.volatility import VOLATILITY_FACTORS
from shared.factors.derivatives import DERIVATIVES_FACTORS
from shared.factors.sentiment import SENTIMENT_FACTORS
from shared.factors.kimchi_premium import KIMCHI_PREMIUM_FACTORS

ALL_FACTORS: list[Factor] = (
    TECHNICAL_FACTORS
    + MOMENTUM_FACTORS
    + MEAN_REVERSION_FACTORS
    + VOLATILITY_FACTORS
    + DERIVATIVES_FACTORS
    + SENTIMENT_FACTORS
    + KIMCHI_PREMIUM_FACTORS
)


def get_all() -> list[Factor]:
    """Return all registered factors."""
    return ALL_FACTORS


def get_by_category(category: str) -> list[Factor]:
    """Return factors filtered by category."""
    return [f for f in ALL_FACTORS if f.category == category]


def compute_all(features: dict) -> dict[str, float]:
    """Compute all factors and return a name -> score mapping."""
    return {f.name: f.compute(features) for f in ALL_FACTORS}
