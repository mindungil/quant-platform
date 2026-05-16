"""Basis-trading factors — spot-perp, futures term structure, cross-exchange.

Each factor takes the standard `features: dict` argument and emits a
score in [-1, 1] aligned with the canonical sign convention:

  - **Positive score** when the basis/spread is *abnormally compressed*
    or favorable to a LONG basis (long-perp / short-spot, or long-near /
    short-far for term structure).
  - **Negative score** when the basis is *abnormally wide* in the
    expensive-near direction — a typical mean-reversion entry.

Factors use tanh squashing on a z-score so a momentary 1σ deviation
doesn't peg the score at ±1. The threshold (`scale_bp`) controls how
many basis points map to the half-saturation point.

Designed to plug into shared.factors.ALL_FACTORS via the standard
register_factors() entry point. Public (academic-standard formulation).
"""
from __future__ import annotations

import math

from shared.factors.base import Factor


class SpotPerpBasisFactor(Factor):
    """Score the spot-perp basis. Positive = perp cheap (long-perp bias)."""

    def __init__(self, scale_bp: float = 30.0) -> None:
        super().__init__(
            name="spot_perp_basis",
            category="derivatives",
            description="Spot-perp basis in bp, tanh-scored. + = perp cheap.",
        )
        self._scale_bp = scale_bp

    def compute(self, features: dict) -> float:
        spot = self._safe_get(features, "spot_close", 0.0)
        perp = self._safe_get(features, "perp_close", 0.0)
        if spot <= 0 or perp <= 0:
            return 0.0
        basis_bp = (perp - spot) / spot * 10_000.0
        # Flip sign: perp cheap → basis_bp negative → score positive.
        return self._tanh_norm(-basis_bp, scale=self._scale_bp)


class TermStructureFactor(Factor):
    """Score the 1-month vs 3-month futures term structure.

    Positive when the curve is *backwardated* (near > far) — interpreted
    as supply scarcity / spot-buying pressure → bullish near-term.
    Negative when contango (near < far).
    """

    def __init__(self, scale_bp: float = 50.0) -> None:
        super().__init__(
            name="term_structure",
            category="derivatives",
            description="(near - far)/far in bp, tanh-scored. + = backwardation.",
        )
        self._scale_bp = scale_bp

    def compute(self, features: dict) -> float:
        near = self._safe_get(features, "futures_1m", 0.0)
        far = self._safe_get(features, "futures_3m", 0.0)
        if near <= 0 or far <= 0:
            return 0.0
        spread_bp = (near - far) / far * 10_000.0
        return self._tanh_norm(spread_bp, scale=self._scale_bp)


class CrossExchangeSpreadFactor(Factor):
    """Score the cross-exchange spread for the same asset.

    Positive when the *secondary* exchange trades materially above the
    primary — historical hint of inflows hitting one venue first. A
    persistent spread is more meaningful than a momentary tick (use
    a smoothed feature upstream if available).
    """

    def __init__(
        self,
        primary_key: str = "binance_close",
        secondary_key: str = "coinbase_close",
        scale_bp: float = 25.0,
    ) -> None:
        super().__init__(
            name=f"xspread_{secondary_key[:-6]}_{primary_key[:-6]}",
            category="derivatives",
            description=f"({secondary_key} - {primary_key})/{primary_key} in bp.",
        )
        self._primary_key = primary_key
        self._secondary_key = secondary_key
        self._scale_bp = scale_bp

    def compute(self, features: dict) -> float:
        primary = self._safe_get(features, self._primary_key, 0.0)
        secondary = self._safe_get(features, self._secondary_key, 0.0)
        if primary <= 0 or secondary <= 0:
            return 0.0
        spread_bp = (secondary - primary) / primary * 10_000.0
        return self._tanh_norm(spread_bp, scale=self._scale_bp)


# Default pack — the registry imports this for the bundled deriv factor set.
BASIS_FACTORS = [
    SpotPerpBasisFactor(),
    TermStructureFactor(),
    CrossExchangeSpreadFactor(),
]
