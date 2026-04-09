"""Tests for performance attribution."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha import get_alpha
from shared.backtest.synthetic import generate_synthetic_ohlcv
from shared.portfolio import EnsembleAllocator, EnsembleConfig, attribute


@pytest.fixture
def ensemble_setup():
    df = generate_synthetic_ohlcv(n_bars=2500, seed=7, trend_strength=8.0)
    ret = df["close"].pct_change().fillna(0.0)
    alpha_pos = {
        name: get_alpha(name).generate(df).position
        for name in ["trend_breakout", "momentum_ensemble", "mean_reversion"]
    }
    alloc = EnsembleAllocator(EnsembleConfig(combine_mode="inverse_vol", periods_per_year=24 * 365))
    res = alloc.combine(alpha_pos, ret)
    return res, alpha_pos, ret


def test_attribution_produces_per_alpha_rows(ensemble_setup):
    res, alpha_pos, ret = ensemble_setup
    report = attribute(res, alpha_pos, ret, periods_per_year=24 * 365)
    assert not report.per_alpha.empty
    # All input alphas should appear (plus _overlay)
    for name in ("trend_breakout", "momentum_ensemble", "mean_reversion", "_overlay"):
        assert name in report.per_alpha.index


def test_attribution_metrics_are_finite(ensemble_setup):
    res, alpha_pos, ret = ensemble_setup
    report = attribute(res, alpha_pos, ret, periods_per_year=24 * 365)
    for col in ("cumulative_pnl", "sharpe", "max_drawdown"):
        assert report.per_alpha[col].apply(np.isfinite).all()


def test_attribution_handles_empty():
    from shared.portfolio.ensemble import EnsembleResult
    empty_res = EnsembleResult(
        target_position=pd.Series(dtype=float),
        raw_combined=pd.Series(dtype=float),
        alpha_weights=pd.DataFrame(),
    )
    report = attribute(empty_res, {}, pd.Series(dtype=float))
    assert report.n_alphas == 0
