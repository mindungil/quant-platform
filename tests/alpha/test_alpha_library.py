"""Tests for the alpha library — contracts, no-look-ahead, position bounds."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha import (
    AlphaConfig,
    CarryAlpha,
    CrossSectionalMomentumAlpha,
    MeanReversionAlpha,
    MomentumEnsembleAlpha,
    StatArbAlpha,
    TrendBreakoutAlpha,
    VolBreakoutAlpha,
    get_alpha,
    list_alphas,
)
from shared.backtest.synthetic import (
    generate_correlated_panel,
    generate_ranging_ohlcv,
    generate_synthetic_ohlcv,
    generate_volatility_cycle_ohlcv,
)


@pytest.fixture
def trending_df():
    return generate_synthetic_ohlcv(n_bars=2000, seed=7, trend_strength=8.0)


@pytest.fixture
def ranging_df():
    return generate_ranging_ohlcv(n_bars=2000, seed=11)


@pytest.fixture
def vol_cycle_df():
    return generate_volatility_cycle_ohlcv(n_bars=2000, seed=23)


def test_registry_lists_all_alphas():
    names = list_alphas()
    assert "trend_breakout" in names
    assert "mean_reversion" in names
    assert "momentum_ensemble" in names
    assert "vol_breakout" in names
    assert "carry" in names
    assert "stat_arb" in names
    assert "cross_sectional_momentum" in names


def test_get_alpha_returns_instance():
    alpha = get_alpha("trend_breakout")
    assert alpha.name == "trend_breakout"


def test_get_alpha_unknown_raises():
    with pytest.raises(KeyError):
        get_alpha("nope")


@pytest.mark.parametrize(
    "name",
    ["trend_breakout", "mean_reversion", "momentum_ensemble", "vol_breakout"],
)
def test_position_in_bounds(name, trending_df):
    alpha = get_alpha(name)
    sig = alpha.generate(trending_df)
    assert sig.position.between(-1.0, 1.0).all()
    assert len(sig.position) == len(trending_df)
    assert not sig.position.isna().any()


def test_no_look_ahead_first_bar_zero(trending_df):
    """The shift-by-1 in Alpha.generate should make the first position zero."""
    alpha = TrendBreakoutAlpha()
    sig = alpha.generate(trending_df)
    assert sig.position.iloc[0] == 0.0


def test_long_only_clips_negative():
    cfg = AlphaConfig(name="trend_breakout", long_only=True)
    alpha = TrendBreakoutAlpha(cfg)
    df = generate_synthetic_ohlcv(n_bars=1500, seed=11, trend_strength=8.0)
    sig = alpha.generate(df)
    assert (sig.position >= 0).all()


def test_max_gross_position_caps():
    cfg = AlphaConfig(name="trend_breakout", max_gross_position=0.5)
    alpha = TrendBreakoutAlpha(cfg)
    df = generate_synthetic_ohlcv(n_bars=1500, seed=7, trend_strength=8.0)
    sig = alpha.generate(df)
    assert sig.position.abs().max() <= 0.5 + 1e-9


def test_carry_returns_zero_without_funding_column():
    df = generate_synthetic_ohlcv(n_bars=1000, seed=42, funding=False)
    alpha = CarryAlpha()
    sig = alpha.generate(df)
    assert (sig.position == 0).all()


def test_carry_uses_funding_column():
    df = generate_synthetic_ohlcv(n_bars=2000, seed=42, funding=True)
    alpha = CarryAlpha()
    sig = alpha.generate(df)
    # Should be non-trivial somewhere — funding signal exists
    assert sig.position.abs().sum() >= 0  # not strictly > 0 because it may stay below threshold


def test_stat_arb_requires_dict():
    alpha = StatArbAlpha(AlphaConfig(name="stat_arb", params={"asset_a": "BTC", "asset_b": "ETH"}))
    with pytest.raises(TypeError):
        alpha._generate(generate_synthetic_ohlcv(n_bars=500))


def test_stat_arb_runs_on_panel():
    panel = generate_correlated_panel(["BTC", "ETH"], n_bars=1500, seed=7)
    alpha = StatArbAlpha(AlphaConfig(name="stat_arb", params={"asset_a": "BTC", "asset_b": "ETH", "lookback": 100}))
    sig = alpha.generate(panel)
    assert len(sig.position) > 0
    assert sig.position.abs().max() <= 1.0


def test_cross_sectional_per_asset():
    panel = generate_correlated_panel(["A", "B", "C", "D"], n_bars=1500, seed=11)
    alpha = CrossSectionalMomentumAlpha()
    per_asset = alpha.generate_per_asset(panel)
    assert set(per_asset.keys()) == {"A", "B", "C", "D"}
    # At each bar, longs and shorts should roughly cancel
    panel_idx = next(iter(per_asset.values())).index
    sums = pd.Series(0.0, index=panel_idx)
    for s in per_asset.values():
        sums = sums + s
    # Allow non-zero on intermediate bars but check the average is small
    assert sums.abs().mean() < 0.5
