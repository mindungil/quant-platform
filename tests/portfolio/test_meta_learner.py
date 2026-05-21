"""Tests for the online meta-learner + alpha tracker."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# V14: alpha_tracker is IP-split; skip cleanly in the public build.
pytest.importorskip(
    "shared.portfolio.alpha_tracker",
    reason="alpha_tracker is IP-split (not in public build)",
)

from shared.portfolio.alpha_tracker import (
    AlphaTracker,
    batch_rolling_diagnostics,
    dead_alphas,
)
from shared.portfolio.meta_learner import (
    MetaLearnerConfig,
    OnlineMetaLearner,
    _inverse_vol_weights,
    _project_simplex_with_cap,
)


def test_simplex_projection_respects_cap():
    w = np.array([0.9, 0.1, 0.0, 0.0])
    out = _project_simplex_with_cap(w, cap=0.5)
    assert abs(out.sum() - 1.0) < 1e-6
    assert (out <= 0.5 + 1e-9).all()


def test_inverse_vol_equal_when_variances_equal():
    rng = np.random.default_rng(7)
    returns = rng.normal(0, 0.01, (500, 4))
    w = _inverse_vol_weights(returns)
    assert abs(w.sum() - 1.0) < 1e-9
    assert np.allclose(w, 0.25, atol=0.05)


def test_meta_learner_weights_drift_toward_better_alpha():
    """Feed one consistently positive alpha and two noise alphas.
    After enough steps, the good alpha should get more weight than the average bad."""
    rng = np.random.default_rng(0)
    ml = OnlineMetaLearner(["good", "bad1", "bad2"], MetaLearnerConfig(eta=2.0, max_per_alpha=0.6))
    for _ in range(800):
        pnl = {
            "good": float(rng.normal(0.005, 0.005)),
            "bad1": float(rng.normal(-0.003, 0.005)),
            "bad2": float(rng.normal(-0.003, 0.005)),
        }
        ml.step(pnl)
    w = ml.weights
    avg_bad = 0.5 * (w["bad1"] + w["bad2"])
    assert w["good"] > avg_bad
    assert w["good"] <= 0.6 + 1e-9


def test_meta_learner_dead_alpha_gets_zero_weight():
    ml = OnlineMetaLearner(["a", "b", "c"])
    ml.step({"a": 0.01, "b": 0.01, "c": 0.01}, dead_alphas={"c"})
    assert ml.weights["c"] == 0.0
    assert abs(sum(ml.weights.values()) - 1.0) < 1e-6


def test_alpha_tracker_flags_dead_after_persistent_negative_sharpe():
    tracker = AlphaTracker(
        name="bad",
        window=200,
        min_history=50,
        dead_persistence=40,
        kill_sharpe=-0.2,
        max_dd_kill=0.95,
    )
    rng = np.random.default_rng(7)
    last_health = None
    for _ in range(400):
        # Consistently negative PnL — should trip the dead flag
        last_health = tracker.update(float(rng.normal(-0.002, 0.001)))
    assert last_health is not None
    assert last_health.is_dead
    assert last_health.rolling_sharpe < 0


def test_alpha_tracker_stays_alive_for_positive_alpha():
    tracker = AlphaTracker(name="good", window=200, min_history=50, dead_persistence=40)
    rng = np.random.default_rng(1)
    last_health = None
    for _ in range(400):
        last_health = tracker.update(float(rng.normal(0.002, 0.005)))
    assert not last_health.is_dead


def test_batch_diagnostics_shapes():
    rng = np.random.default_rng(11)
    idx = pd.date_range("2024-01-01", periods=500, freq="h")
    df = pd.DataFrame(rng.normal(0, 0.01, (500, 3)), columns=["x", "y", "z"], index=idx)
    diag = batch_rolling_diagnostics(df, window=100)
    assert set(diag.keys()) == {"x", "y", "z"}
    for name, d in diag.items():
        assert "sharpe" in d.columns
        assert "max_dd" in d.columns
        assert len(d) == len(df)


def test_dead_alphas_returns_first_bar():
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=800, freq="h")
    good = rng.normal(0.002, 0.01, 800)
    bad = rng.normal(-0.005, 0.005, 800)
    df = pd.DataFrame({"good": good, "bad": bad}, index=idx)
    res = dead_alphas(df, window=200, kill_sharpe=-0.2, persistence=50, max_dd_kill=0.5)
    assert "bad" in res
    assert "good" not in res


def test_fallback_activates_on_extreme_variance_ratio():
    ml = OnlineMetaLearner(["a", "b"], MetaLearnerConfig(fallback_var_ratio=10.0, min_history=50))
    rng = np.random.default_rng(7)
    recent = pd.DataFrame({
        "a": rng.normal(0, 0.001, 100),
        "b": rng.normal(0, 1.0, 100),  # 1000× larger std
    })
    ml.step_with_fallback({"a": 0.01, "b": 0.01}, recent_pnl=recent)
    assert ml.used_fallback_last_step
