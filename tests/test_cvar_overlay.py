"""Integration tests for CVaR overlay in meta_ensemble."""
import numpy as np
import pandas as pd
import pytest

# V14: cvar_overlay is IP-split (not present in the public build).
# Skip cleanly so collection doesn't error.
try:
    from shared.portfolio.meta_ensemble import (
        MetaEnsembleConfig,
        combine,
        cvar_overlay,
    )
except ImportError:
    pytest.skip(
        "cvar_overlay is IP-split (not in public build)",
        allow_module_level=True,
    )


@pytest.fixture
def synthetic_data():
    np.random.seed(42)
    n = 2000
    idx = pd.RangeIndex(n)
    alphas = pd.DataFrame(
        {"a1": np.sign(np.random.normal(0, 1, n)), "a2": np.sign(np.random.normal(0, 1, n))},
        index=idx,
    )
    ret = pd.Series(np.random.normal(0, 0.01, n), index=idx)
    return alphas, ret


class TestCvarOverlayFunction:
    def test_disabled_returns_ones(self):
        pnl = pd.Series(np.random.normal(0, 0.01, 500))
        result = cvar_overlay(pnl, target_bps=0)
        assert np.allclose(result.values, 1.0)

    def test_enabled_scales_down(self):
        np.random.seed(1)
        pnl = pd.Series(np.random.normal(-0.001, 0.02, 1000))
        result = cvar_overlay(pnl, target_bps=10, alpha=0.95, lookback=200)
        assert result.min() >= 0.09  # floor = 0.1 default
        assert result.mean() < 1.0   # should be scaling down

    def test_shift_prevents_lookahead(self):
        pnl = pd.Series(np.random.normal(0, 0.01, 500))
        result = cvar_overlay(pnl, target_bps=20, lookback=100)
        # First bar must be 1.0 (shift=1 means no info yet)
        assert result.iloc[0] == 1.0

    def test_floor_respected(self):
        pnl = pd.Series(np.random.normal(-0.05, 0.1, 1000))
        result = cvar_overlay(pnl, target_bps=1, floor=0.2, lookback=200)
        assert result.min() >= 0.19  # allow small float tolerance


class TestCombineWithCvar:
    def test_disabled_no_regression(self, synthetic_data):
        alphas, ret = synthetic_data
        r0 = combine(alphas, ret, config=MetaEnsembleConfig(cvar_target_bps=0))
        assert np.allclose(r0["cvar_multiplier"].values, 1.0)

    def test_enabled_differs(self, synthetic_data):
        alphas, ret = synthetic_data
        r0 = combine(alphas, ret, config=MetaEnsembleConfig(cvar_target_bps=0))
        r1 = combine(alphas, ret, config=MetaEnsembleConfig(cvar_target_bps=20))
        assert not np.allclose(r0["position"].values, r1["position"].values)

    def test_cvar_reduces_tail(self, synthetic_data):
        alphas, ret = synthetic_data
        r0 = combine(alphas, ret, config=MetaEnsembleConfig(cvar_target_bps=0))
        r1 = combine(alphas, ret, config=MetaEnsembleConfig(cvar_target_bps=15))
        pnl0 = (r0["position"] * ret).values
        pnl1 = (r1["position"] * ret).values
        # CVaR-constrained should have smaller or equal tail loss
        q = 0.05
        tail0 = np.sort(pnl0)[: int(len(pnl0) * q)].mean()
        tail1 = np.sort(pnl1)[: int(len(pnl1) * q)].mean()
        # tail1 should be less negative (better) or equal
        assert tail1 >= tail0 - 1e-6
