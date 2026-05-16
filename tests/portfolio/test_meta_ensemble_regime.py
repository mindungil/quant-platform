"""Regime-conditional MV weights in shared.portfolio.meta_ensemble.

Validates the new Phase G' path: per-bar weights vary by regime label
when use_regime_conditional_weights=True, and the table falls back to
pooled weights for under-sampled regimes.

This file is IP (touches meta_ensemble.py which is private) — listed in
ops/private_paths.txt so it stays in the IP overlay.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.portfolio.meta_ensemble import (
    MetaEnsembleConfig,
    combine,
    compute_regime_alpha_weights,
    expand_regime_weights_to_panel,
    sharpe_filtered_mv_weights,
)


def _make_regime_separable_panel(n: int = 1000, seed: int = 0) -> tuple[
    pd.DataFrame, pd.Series, pd.Series
]:
    """Build a panel where each alpha excels in exactly one regime.

    - trend_alpha: positive PnL in TREND_UP, zero elsewhere
    - mean_rev_alpha: positive PnL in RANGE, negative in TREND_UP
    - vol_alpha: positive in CRISIS, near-zero elsewhere
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    # Regime label per bar — block-structured so each regime has ~n/3 samples
    labels = np.array(
        ["TREND_UP"] * (n // 3) + ["RANGE"] * (n // 3) + ["CRISIS"] * (n - 2 * (n // 3))
    )
    rng.shuffle(labels)
    regime = pd.Series(labels, index=idx)

    bar_ret = pd.Series(rng.normal(0, 0.01, n), index=idx)

    # Per-alpha positions designed to make Sharpe pop in the matching regime
    trend_pos = np.where(labels == "TREND_UP", 1.0, 0.0)
    mr_pos = np.where(labels == "RANGE", 1.0, np.where(labels == "TREND_UP", -0.5, 0.0))
    vol_pos = np.where(labels == "CRISIS", 1.0, 0.0)

    # Boost realized PnL in matching regime so MV picks them up
    boosted_ret = bar_ret.copy()
    boosted_ret[labels == "TREND_UP"] += 0.003   # positive trend
    boosted_ret[labels == "RANGE"] += 0.0        # flat
    boosted_ret[labels == "CRISIS"] -= 0.002     # crisis = down

    panel = pd.DataFrame(
        {
            "trend_alpha": pd.Series(trend_pos, index=idx),
            "mean_rev_alpha": pd.Series(mr_pos, index=idx),
            "vol_alpha": pd.Series(vol_pos, index=idx),
        }
    )
    return panel, boosted_ret, regime


def test_compute_regime_alpha_weights_basic() -> None:
    """3 regimes × 3 alphas → returns a weight dict keyed by regime."""
    panel, ret, regime = _make_regime_separable_panel()
    pnl_panel = panel.mul(ret, axis=0).fillna(0.0)
    weights_by_regime = compute_regime_alpha_weights(
        pnl_panel,
        regime,
        min_regime_samples=50,
    )
    assert set(weights_by_regime.keys()) >= {"TREND_UP", "RANGE", "CRISIS"}
    for label, w in weights_by_regime.items():
        assert isinstance(w, pd.Series)
        assert set(w.index) == {"trend_alpha", "mean_rev_alpha", "vol_alpha"}
        # Weights are non-negative and sum to ~1 (long-only post-clip normalization)
        assert (w >= -1e-9).all()
        assert abs(w.sum() - 1.0) < 0.1 or w.sum() == 0  # MV can fail → eq fallback


def test_regime_conditional_picks_specialist_alpha_per_regime() -> None:
    """trend_alpha should dominate TREND_UP weight; vol_alpha — but wait,
    vol_alpha has negative PnL in CRISIS, so it gets dropped. The point
    being: an alpha with positive Sharpe in regime X gets nonzero weight
    in regime X (specifically, larger than its global weight)."""
    panel, ret, regime = _make_regime_separable_panel(n=2000)
    pnl_panel = panel.mul(ret, axis=0).fillna(0.0)
    weights_by_regime = compute_regime_alpha_weights(
        pnl_panel,
        regime,
        min_regime_samples=100,
    )
    # In TREND_UP, trend_alpha gets +0.003 per bar, mr_alpha gets -0.0015.
    # MV should give trend_alpha the lion's share.
    trend_w_in_trend = weights_by_regime["TREND_UP"]["trend_alpha"]
    mr_w_in_trend = weights_by_regime["TREND_UP"]["mean_rev_alpha"]
    assert trend_w_in_trend > mr_w_in_trend, (
        f"trend_alpha should win in TREND_UP, got {trend_w_in_trend} vs {mr_w_in_trend}"
    )


def test_regime_conditional_fallback_for_undersampled_regime() -> None:
    """Regime with < min_regime_samples bars → global weight, not zero."""
    panel, ret, _regime = _make_regime_separable_panel(n=500)
    pnl_panel = panel.mul(ret, axis=0).fillna(0.0)
    # Construct a regime where 'RARE' appears only 5 times — under threshold
    bar_labels = ["MAIN"] * 495 + ["RARE"] * 5
    np.random.default_rng(0).shuffle(bar_labels)
    regime = pd.Series(bar_labels, index=panel.index)
    out = compute_regime_alpha_weights(
        pnl_panel, regime, min_regime_samples=100, fallback_to_global=True
    )
    assert "MAIN" in out
    assert "RARE" in out
    # RARE fell back to global — weights should be identical to a fresh global call
    global_w = sharpe_filtered_mv_weights(pnl_panel)
    pd.testing.assert_series_equal(
        out["RARE"].sort_index(),
        global_w.sort_index(),
        check_names=False,
    )


def test_expand_regime_weights_to_panel_shape_and_routing() -> None:
    """expand → DataFrame of (n_bars × n_alphas) with each bar's regime weight."""
    cols = ["a", "b", "c"]
    table = {
        "X": pd.Series({"a": 0.5, "b": 0.5, "c": 0.0}),
        "Y": pd.Series({"a": 0.0, "b": 0.0, "c": 1.0}),
    }
    regime = pd.Series(["X", "Y", "X", "Y"], index=pd.RangeIndex(4))
    panel = expand_regime_weights_to_panel(table, regime, cols)
    assert panel.shape == (4, 3)
    assert panel.iloc[0]["a"] == 0.5
    assert panel.iloc[1]["c"] == 1.0
    assert panel.iloc[2]["b"] == 0.5
    assert panel.iloc[3]["a"] == 0.0


def test_combine_regime_path_returns_filled_regime_table() -> None:
    """combine(use_regime_conditional_weights=True) populates regime_alpha_weights."""
    panel, ret, regime = _make_regime_separable_panel(n=1500)
    cfg = MetaEnsembleConfig(
        use_regime_conditional_weights=True,
        regime_min_samples=100,
        kelly_min_samples=50,
    )
    out = combine(panel, ret, regime=regime, config=cfg)
    assert "regime_alpha_weights" in out
    table = out["regime_alpha_weights"]
    assert {"TREND_UP", "RANGE", "CRISIS"} & set(table.keys())
    # Position series should be finite
    assert out["position"].notna().all()


def test_combine_regime_off_is_backward_compatible() -> None:
    """Default (use_regime_conditional_weights=False) → empty regime table,
    behaves like the pre-existing global-weight path."""
    panel, ret, regime = _make_regime_separable_panel(n=1500)
    cfg = MetaEnsembleConfig(kelly_min_samples=50)  # default = off
    out = combine(panel, ret, regime=regime, config=cfg)
    assert out["regime_alpha_weights"] == {}
    assert "position" in out and out["position"].notna().all()
