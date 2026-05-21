"""Tests for regime-conditional attribution."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# V14: attribution_regime is IP-split; skip cleanly in the public build.
pytest.importorskip(
    "shared.portfolio.attribution_regime",
    reason="attribution_regime is IP-split (not in public build)",
)

from shared.alpha import get_alpha
from shared.backtest.synthetic import generate_synthetic_ohlcv
from shared.portfolio import EnsembleAllocator, EnsembleConfig
from shared.portfolio.attribution_regime import ALL_STATES, attribute_by_regime


def test_regime_attribution_covers_all_states_column_shape():
    df = generate_synthetic_ohlcv(n_bars=2500, seed=7, trend_strength=6.0)
    ret = df["close"].pct_change().fillna(0.0)
    alpha_pos = {
        "trend_breakout": get_alpha("trend_breakout", allow_blocked=True).generate(df).position,
        "momentum_ensemble": get_alpha("momentum_ensemble").generate(df).position,
    }
    alloc = EnsembleAllocator(EnsembleConfig(combine_mode="inverse_vol", periods_per_year=24 * 365))
    res = alloc.combine(alpha_pos, ret)
    report = attribute_by_regime(res, alpha_pos, ret, price_df=df)
    # Shape: rows = alphas, columns = 9 regime states
    assert list(report.per_alpha_regime.columns) == ALL_STATES
    assert "trend_breakout" in report.per_alpha_regime.index
    # Coverage sums to 1 (up to ffill)
    assert abs(report.bar_coverage.sum() - 1.0) < 0.05


def test_regime_attribution_empty_ensemble():
    from shared.portfolio.ensemble import EnsembleResult

    empty = EnsembleResult(
        target_position=pd.Series(dtype=float),
        raw_combined=pd.Series(dtype=float),
        alpha_weights=pd.DataFrame(),
    )
    report = attribute_by_regime(empty, {}, pd.Series(dtype=float))
    assert report.n_alphas == 0
