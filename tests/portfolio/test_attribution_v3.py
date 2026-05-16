"""Tests for V3 attribution additions in shared/portfolio/attribution.py.

attribute_by_regime, flag_dead_alphas, rolling_attribution_sharpe.

IP test (attribution.py is private — listed in ops/private_paths.txt).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from shared.portfolio.attribution import (
    AttributionReport,
    attribute,
    attribute_by_regime,
    flag_dead_alphas,
    rolling_attribution_sharpe,
)


# Minimal stub of EnsembleResult so we don't import the full ensemble.py
@dataclass
class _StubEnsemble:
    alpha_weights: pd.DataFrame
    target_position: pd.Series


def _make_simple_ensemble(
    n: int = 500,
    alpha_names=("a_trend", "a_mr"),
    seed: int = 0,
) -> tuple[_StubEnsemble, dict[str, pd.Series], pd.Series, pd.Series]:
    """Build a small panel with a clear regime separation:
    a_trend wins in TREND, a_mr wins in RANGE."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    weights = pd.DataFrame(0.5, index=idx, columns=list(alpha_names))
    target = pd.Series(1.0, index=idx)

    # Regime: half TREND, half RANGE, interleaved blocks
    labels = np.array(["TREND"] * (n // 2) + ["RANGE"] * (n - n // 2))
    rng.shuffle(labels)
    regime = pd.Series(labels, index=idx)

    # Returns: TREND has positive drift, RANGE is zero-mean noise
    ret_arr = np.where(labels == "TREND",
                       rng.normal(0.002, 0.005, n),
                       rng.normal(0.0, 0.005, n))
    underlying_returns = pd.Series(ret_arr, index=idx)

    # Positions: trend alpha = +1 in TREND, 0 elsewhere
    #            mr alpha = positive position in RANGE (zero-mean ret → still ~0 PnL)
    a_trend_pos = pd.Series(np.where(labels == "TREND", 1.0, 0.0), index=idx)
    a_mr_pos = pd.Series(np.where(labels == "RANGE", 1.0, 0.0), index=idx)
    positions = {"a_trend": a_trend_pos, "a_mr": a_mr_pos}

    ensemble = _StubEnsemble(alpha_weights=weights, target_position=target)
    return ensemble, positions, underlying_returns, regime


# ──────────────────────────────────────────────────────────────────
# attribute_by_regime
# ──────────────────────────────────────────────────────────────────


def test_attribute_by_regime_shape_and_labels() -> None:
    ens, pos, ret, regime = _make_simple_ensemble(n=400, seed=1)
    out = attribute_by_regime(ens, pos, ret, regime)
    assert out.n_alphas == 2
    assert out.n_regimes == 2
    assert set(out.cumulative_by_regime.columns) == {"TREND", "RANGE"}
    assert set(out.cumulative_by_regime.index) == {"a_trend", "a_mr"}


def test_attribute_by_regime_trend_alpha_wins_in_trend() -> None:
    ens, pos, ret, regime = _make_simple_ensemble(n=1500, seed=2)
    out = attribute_by_regime(ens, pos, ret, regime)
    assert out.cumulative_by_regime.loc["a_trend", "TREND"] > 0
    # a_trend should NOT have meaningful PnL in RANGE (its position there is 0)
    assert abs(out.cumulative_by_regime.loc["a_trend", "RANGE"]) < 1e-6


def test_attribute_by_regime_empty_inputs_dont_crash() -> None:
    empty_ens = _StubEnsemble(alpha_weights=pd.DataFrame(), target_position=pd.Series(dtype=float))
    out = attribute_by_regime(empty_ens, {}, pd.Series(dtype=float), pd.Series(dtype=str))
    assert out.n_alphas == 0
    assert out.cumulative_by_regime.empty


def test_attribute_by_regime_to_dict_serializable() -> None:
    ens, pos, ret, regime = _make_simple_ensemble(n=300, seed=3)
    out = attribute_by_regime(ens, pos, ret, regime)
    d = out.to_dict()
    assert "cumulative_by_regime" in d
    assert "sharpe_by_regime" in d
    assert d["n_alphas"] == 2


# ──────────────────────────────────────────────────────────────────
# flag_dead_alphas
# ──────────────────────────────────────────────────────────────────


def test_flag_dead_alphas_returns_negative_sharpe_alphas() -> None:
    df = pd.DataFrame({
        "cumulative_pnl": [0.05, -0.03, 0.001],
        "sharpe": [1.2, -0.5, 0.05],
        "hit_ratio": [0.55, 0.30, 0.45],
        "max_drawdown": [0.02, 0.08, 0.05],
        "avg_weight": [0.5, 0.3, 0.4],
        "weight_turnover": [0.1, 0.2, 0.1],
    }, index=["alpha_good", "alpha_bad", "alpha_marginal"])
    dead = flag_dead_alphas(df, sharpe_threshold=0.0, require_negative_pnl=True,
                            min_hit_ratio=0.40)
    assert dead == ["alpha_bad"]


def test_flag_dead_alphas_ignores_overlay_row() -> None:
    df = pd.DataFrame({
        "cumulative_pnl": [-0.1, -0.01],
        "sharpe": [-0.3, -0.2],
        "hit_ratio": [0.30, 0.35],
        "max_drawdown": [0.05, 0.01],
        "avg_weight": [0.5, 0.0],
        "weight_turnover": [0.1, 0.0],
    }, index=["alpha_bad", "_overlay"])
    dead = flag_dead_alphas(df)
    assert dead == ["alpha_bad"]
    assert "_overlay" not in dead


def test_flag_dead_alphas_empty_input() -> None:
    assert flag_dead_alphas(pd.DataFrame()) == []


def test_flag_dead_alphas_min_hit_ratio_gates() -> None:
    """An alpha with bad Sharpe but high hit ratio shouldn't be flagged
    (it's losing big but winning often — likely a position-sizing
    bug, not a dead alpha)."""
    df = pd.DataFrame({
        "cumulative_pnl": [-0.1],
        "sharpe": [-0.2],
        "hit_ratio": [0.60],  # above default min_hit_ratio=0.40
        "max_drawdown": [0.1],
        "avg_weight": [0.3],
        "weight_turnover": [0.05],
    }, index=["alpha_skewed"])
    assert flag_dead_alphas(df) == []


# ──────────────────────────────────────────────────────────────────
# rolling_attribution_sharpe
# ──────────────────────────────────────────────────────────────────


def test_rolling_sharpe_shape() -> None:
    ens, pos, ret, _ = _make_simple_ensemble(n=400, seed=4)
    rs = rolling_attribution_sharpe(ens, pos, ret, window=100,
                                    periods_per_year=24 * 365)
    assert rs.shape == (400, 2)
    assert set(rs.columns) == {"a_trend", "a_mr"}


def test_rolling_sharpe_picks_up_winning_alpha() -> None:
    ens, pos, ret, _ = _make_simple_ensemble(n=1500, seed=5)
    rs = rolling_attribution_sharpe(ens, pos, ret, window=200,
                                    periods_per_year=24 * 365)
    # Tail of series — should reflect the steady-state Sharpe pattern
    assert rs["a_trend"].iloc[-1] > rs["a_mr"].iloc[-1]


def test_rolling_sharpe_empty_inputs() -> None:
    empty_ens = _StubEnsemble(alpha_weights=pd.DataFrame(), target_position=pd.Series(dtype=float))
    rs = rolling_attribution_sharpe(empty_ens, {}, pd.Series(dtype=float))
    assert rs.empty
