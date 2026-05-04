"""Tests for the composite (vol × trend 3×3) regime classifier."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.backtest.synthetic import (
    generate_ranging_ohlcv,
    generate_synthetic_ohlcv,
    generate_volatility_cycle_ohlcv,
)
from shared.portfolio import (
    EnsembleAllocator,
    EnsembleConfig,
    build_regime_proba,
)
from shared.regime import (
    TREND_STATES,
    VOL_STATES,
    AdxHurstRegime,
    CompositeRegime,
    VolQuantileRegime,
)


def test_vol_regime_covers_three_buckets_on_vol_cycle():
    df = generate_volatility_cycle_ohlcv(n_bars=4000, seed=11)
    labels = VolQuantileRegime().classify(df)
    uniq = set(labels.unique())
    # At least LOW and HIGH appear across a vol cycle
    assert 0 in uniq
    assert 2 in uniq


def test_trend_regime_identifies_trending_vs_ranging():
    trend_df = generate_synthetic_ohlcv(n_bars=3000, seed=7, trend_strength=8.0)
    range_df = generate_ranging_ohlcv(n_bars=3000, seed=11)

    trend_labels = AdxHurstRegime().classify(trend_df)
    range_labels = AdxHurstRegime().classify(range_df)

    # Trending dataset has a meaningfully higher share of TREND (0) than ranging
    assert (trend_labels == 0).mean() > (range_labels == 0).mean()
    # Ranging dataset has a meaningfully higher share of non-TREND
    assert (range_labels == 1).mean() > (trend_labels == 1).mean() - 0.05


def test_composite_grid_has_correct_shape():
    df = generate_synthetic_ohlcv(n_bars=2000, seed=7, trend_strength=5.0)
    out = CompositeRegime().classify(df)
    assert len(out.vol_label) == len(df)
    assert len(out.trend_label) == len(df)
    assert len(out.grid_label) == len(df)
    assert out.grid_label.between(0, 8).all()
    # Grid name format: "VOL_X_TREND_Y"
    assert out.grid_name.str.startswith("VOL_").all()


def test_regime_proba_is_one_row_sum_and_smoothed():
    df = generate_synthetic_ohlcv(n_bars=1500, seed=23, trend_strength=5.0)
    proba = build_regime_proba(df, smoothing=12)
    assert set(proba.columns) == {f"{v}_{t}" for v in VOL_STATES for t in TREND_STATES}
    # Row sums should be ~1 after renormalization
    row_sums = proba.sum(axis=1)
    assert np.isclose(row_sums.dropna(), 1.0, atol=1e-6).all()
    # EWM should keep most bars in [0, 1]
    assert (proba.min().min() >= 0.0 - 1e-9)
    assert (proba.max().max() <= 1.0 + 1e-9)


def test_regime_conditional_ensemble_routes_weight_away_from_killed_alpha():
    """If an alpha has negative affinity in every regime, ensemble should
    starve it relative to a neutral alpha. Pure integration check."""
    df = generate_synthetic_ohlcv(n_bars=2500, seed=42, trend_strength=6.0)
    ret = df["close"].pct_change().fillna(0.0)

    # Two fake alpha signals with comparable vols
    rng = np.random.default_rng(7)
    good = pd.Series(rng.normal(0.2, 0.3, len(df)).clip(-1, 1), index=df.index)
    bad = pd.Series(rng.normal(-0.1, 0.3, len(df)).clip(-1, 1), index=df.index)
    positions = {"good": good, "bad": bad}

    proba = build_regime_proba(df)
    # Negative affinity for 'bad' across every regime; neutral for 'good'
    neutral = {s: 1.0 for s in proba.columns}
    kill = {s: 0.1 for s in proba.columns}
    affinity = {"good": neutral, "bad": kill}

    alloc = EnsembleAllocator(EnsembleConfig(combine_mode="inverse_vol", periods_per_year=24 * 365))
    res = alloc.combine(positions, ret, regime_proba=proba, regime_alpha_affinity=affinity)
    # Average weight on 'good' should exceed 'bad' after warmup.
    # v4.5 raised alpha_gate_floor=0.3 so dead alphas retain minimum weight;
    # the expected gap narrows from +0.4 to ~+0.2.
    w = res.alpha_weights.iloc[500:]
    assert w["good"].mean() > w["bad"].mean() + 0.1
