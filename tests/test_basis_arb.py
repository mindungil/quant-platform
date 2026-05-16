"""Tests for shared.alpha.basis_arb and shared.factors.basis_factors.

Public module tests (lives at tests root, not under tests/alpha which is
IP). Validates both the alpha and the three companion factors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha.base import AlphaConfig
from shared.alpha.basis_arb import BasisArbAlpha
from shared.factors.basis_factors import (
    BASIS_FACTORS,
    CrossExchangeSpreadFactor,
    SpotPerpBasisFactor,
    TermStructureFactor,
)


# ──────────────────────────────────────────────────────────────────
# BasisArbAlpha
# ──────────────────────────────────────────────────────────────────


def _make_basis_frame(n: int = 300, basis_bp_path: np.ndarray | None = None) -> pd.DataFrame:
    """Build a (spot, perp) frame given a basis-bp time-series."""
    if basis_bp_path is None:
        rng = np.random.default_rng(42)
        basis_bp_path = rng.normal(0, 20, n)  # zero-mean basis noise
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    rng = np.random.default_rng(7)
    spot = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    perp = spot * (1.0 + basis_bp_path / 10_000.0)
    # close col required by base.generate when auto_vol_target is enabled —
    # use perp price as the canonical close.
    return pd.DataFrame(
        {"spot_close": spot, "perp_close": perp, "close": perp,
         "open": perp, "high": perp * 1.001, "low": perp * 0.999,
         "volume": np.ones(n) * 1e6},
        index=idx,
    )


def test_basis_arb_missing_legs_is_flat() -> None:
    """Frame without spot_close or perp_close → flat signal, no crash."""
    alpha = BasisArbAlpha()
    df = pd.DataFrame({"close": [100.0] * 50}, index=pd.RangeIndex(50))
    sig = alpha.generate(df)
    assert (sig.position == 0.0).all()


def test_basis_arb_rich_perp_yields_negative_position() -> None:
    """An outlier-large positive basis vs noise baseline → short-perp signal."""
    n = 1100
    # First 1000 bars: zero-mean basis noise establishing baseline. Last 100:
    # a sustained +60bp basis — an outlier vs the noise.
    rng = np.random.default_rng(0)
    path = np.concatenate([rng.normal(0, 5, 1000), np.full(100, 60.0)])
    df = _make_basis_frame(n=n, basis_bp_path=path)
    # zscore_window covers most of the noise → step is an outlier (high z)
    alpha = BasisArbAlpha(AlphaConfig(name="t", params={"zscore_window": 500}))
    sig = alpha.generate(df)
    # Bars deep inside the step should be strongly negative (short perp)
    late = sig.position.iloc[-30:]
    assert late.mean() < -0.3, f"expected short-perp tilt, got mean={late.mean()}"


def test_basis_arb_cheap_perp_yields_positive_position() -> None:
    """Outlier-large negative basis (perp cheap) → long-perp signal."""
    n = 1100
    rng = np.random.default_rng(0)
    path = np.concatenate([rng.normal(0, 5, 1000), np.full(100, -60.0)])
    df = _make_basis_frame(n=n, basis_bp_path=path)
    alpha = BasisArbAlpha(AlphaConfig(name="t", params={"zscore_window": 500}))
    sig = alpha.generate(df)
    late = sig.position.iloc[-30:]
    assert late.mean() > 0.3, f"expected long-perp tilt, got mean={late.mean()}"


def test_basis_arb_position_bounded() -> None:
    """tanh + clip → |position| ≤ max_gross_position."""
    df = _make_basis_frame()
    alpha = BasisArbAlpha()
    sig = alpha.generate(df)
    assert sig.position.abs().max() <= alpha.config.max_gross_position + 1e-9


def test_basis_arb_funding_tilts_position() -> None:
    """High positive funding should bias position downward (short perp) on top of basis."""
    n = 300
    df = _make_basis_frame(n=n, basis_bp_path=np.zeros(n))
    df["funding_rate"] = np.linspace(0.0, 0.002, n)  # ramping funding
    alpha = BasisArbAlpha(AlphaConfig(name="t",
                                      params={"zscore_window": 50,
                                              "funding_weight": 0.6}))
    sig = alpha.generate(df)
    # End of ramp: funding way positive → tilt negative
    assert sig.position.iloc[-1] < 0


# ──────────────────────────────────────────────────────────────────
# Factor tests
# ──────────────────────────────────────────────────────────────────


def test_spot_perp_basis_factor_signs() -> None:
    f = SpotPerpBasisFactor(scale_bp=20.0)
    # perp expensive vs spot → factor negative (short bias)
    assert f.compute({"spot_close": 100.0, "perp_close": 100.5}) < 0
    # perp cheap → factor positive
    assert f.compute({"spot_close": 100.0, "perp_close": 99.5}) > 0
    # equal → zero
    assert f.compute({"spot_close": 100.0, "perp_close": 100.0}) == pytest.approx(0.0)


def test_spot_perp_factor_missing_returns_zero() -> None:
    f = SpotPerpBasisFactor()
    assert f.compute({}) == 0.0
    assert f.compute({"spot_close": 100.0}) == 0.0
    assert f.compute({"spot_close": -1.0, "perp_close": 100.0}) == 0.0


def test_term_structure_factor() -> None:
    f = TermStructureFactor()
    # Backwardation (near > far) → positive
    assert f.compute({"futures_1m": 101.0, "futures_3m": 100.0}) > 0
    # Contango → negative
    assert f.compute({"futures_1m": 100.0, "futures_3m": 101.0}) < 0


def test_cross_exchange_spread_factor() -> None:
    f = CrossExchangeSpreadFactor(scale_bp=20.0)
    # secondary trading above primary → positive
    assert f.compute({"binance_close": 100.0, "coinbase_close": 100.5}) > 0
    assert f.compute({"binance_close": 100.0, "coinbase_close": 99.5}) < 0


def test_basis_factors_pack_is_three_items() -> None:
    assert len(BASIS_FACTORS) == 3
    cats = {f.category for f in BASIS_FACTORS}
    assert cats == {"derivatives"}


def test_basis_factor_scores_are_bounded() -> None:
    """Any input should yield a score in [-1, 1] via tanh."""
    for factor in BASIS_FACTORS:
        for feats in [
            {"spot_close": 100.0, "perp_close": 200.0},   # absurd 100% basis
            {"futures_1m": 100.0, "futures_3m": 50.0},    # absurd backwardation
            {"binance_close": 100.0, "coinbase_close": 500.0},
        ]:
            score = factor.compute(feats)
            assert -1.0 <= score <= 1.0
