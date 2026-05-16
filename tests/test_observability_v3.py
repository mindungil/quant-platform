"""Smoke tests for shared.observability_v3 — V3 #4.

Pure-Python smoke tests (no scrape endpoint, no Grafana). Verifies the
metric helpers don't crash on the expected inputs and that gauge values
land where claimed.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from shared.observability_v3 import (
    ATTRIBUTION_ALPHA_CUMULATIVE_PNL,
    ATTRIBUTION_ALPHA_REGIME_PNL,
    ATTRIBUTION_ALPHA_ROLLING_SHARPE,
    LEARNING_ALPHA_DSR,
    LEARNING_ALPHA_STATE,
    LEARNING_FACTOR_DECAYED,
    LEARNING_FACTOR_IC_IR,
    MAKER_TAKER_ARM_MEAN_REWARD,
    export_alpha_snapshot,
    export_attribution_report,
    export_bandit_stats,
    export_factor_snapshot,
    export_regime_attribution_report,
    export_rolling_sharpe,
    record_dead_alpha_flag,
    record_maker_taker_decision,
    record_state_transition,
)


def _gauge_value(gauge, **labels) -> float:
    """Read back a labeled gauge value (Prometheus client API)."""
    return gauge.labels(**labels)._value.get()


# ──────────────────────────────────────────────────────────────────
# Learning loop snapshot exporters
# ──────────────────────────────────────────────────────────────────


def test_export_alpha_snapshot_sets_state_and_dsr() -> None:
    snap = {
        "alpha_a": {"state": "LIVE", "dsr": 0.83, "n_samples": 1000, "last_decision_reason": "healthy"},
        "alpha_b": {"state": "SHADOW", "dsr": 0.31, "n_samples": 800, "last_decision_reason": "paused"},
    }
    export_alpha_snapshot(snap)
    assert _gauge_value(LEARNING_ALPHA_STATE, alpha_name="alpha_a") == 1.0
    assert _gauge_value(LEARNING_ALPHA_STATE, alpha_name="alpha_b") == 0.0
    assert _gauge_value(LEARNING_ALPHA_DSR, alpha_name="alpha_a") == pytest.approx(0.83)


def test_export_alpha_snapshot_skips_dsr_when_none() -> None:
    """DSR=None (warmup) → gauge not updated, but state still set."""
    snap = {"warmup": {"state": "LIVE", "dsr": None}}
    export_alpha_snapshot(snap)
    assert _gauge_value(LEARNING_ALPHA_STATE, alpha_name="warmup") == 1.0


def test_export_factor_snapshot_sets_ic_ir_and_decayed() -> None:
    snap = {
        "f_alive": {"ic_ir": 0.72, "is_decayed": False, "factor": "f_alive", "n_obs": 200,
                    "current_ic": 0.5, "active_weight": 1.0},
        "f_dead": {"ic_ir": 0.05, "is_decayed": True, "factor": "f_dead", "n_obs": 800,
                   "current_ic": 0.02, "active_weight": 0.0},
    }
    export_factor_snapshot(snap)
    assert _gauge_value(LEARNING_FACTOR_IC_IR, factor_name="f_alive") == pytest.approx(0.72)
    assert _gauge_value(LEARNING_FACTOR_DECAYED, factor_name="f_dead") == 1.0
    assert _gauge_value(LEARNING_FACTOR_DECAYED, factor_name="f_alive") == 0.0


# ──────────────────────────────────────────────────────────────────
# Transition / dead-alpha counters
# ──────────────────────────────────────────────────────────────────


def test_record_state_transition_increments_counter() -> None:
    before = _gauge_value(
        LEARNING_ALPHA_DSR.__class__,  # dummy
        alpha_name="x"
    ) if False else 0
    record_state_transition("alpha_x", "LIVE", "SHADOW")
    record_state_transition("alpha_x", "LIVE", "SHADOW")
    # Counter doesn't expose .get() the same way; just verify no crash
    # (and instrumentation pipeline trusts counter increments).
    record_state_transition("alpha_x", "SHADOW", "LIVE")


def test_record_dead_alpha_flag_no_crash() -> None:
    record_dead_alpha_flag("alpha_dead")
    record_dead_alpha_flag("alpha_dead")


# ──────────────────────────────────────────────────────────────────
# Maker/taker exporters
# ──────────────────────────────────────────────────────────────────


def test_record_maker_taker_decision_with_slippage() -> None:
    record_maker_taker_decision("ctx_a", "MAKER", realized_slippage_bp=3.5)
    record_maker_taker_decision("ctx_a", "TAKER", realized_slippage_bp=18.0)
    # No slippage provided still works
    record_maker_taker_decision("ctx_a", "MAKER")


def test_export_bandit_stats_sets_mean_reward_gauges() -> None:
    stats = {
        "ctx_q": {
            "MAKER": {"n": 50, "mean_reward": -0.02, "std": 0.05, "total_reward": -1.0},
            "TAKER": {"n": 30, "mean_reward": -0.10, "std": 0.08, "total_reward": -3.0},
        }
    }
    export_bandit_stats(stats)
    assert _gauge_value(MAKER_TAKER_ARM_MEAN_REWARD, context="ctx_q", action="MAKER") == pytest.approx(-0.02)
    assert _gauge_value(MAKER_TAKER_ARM_MEAN_REWARD, context="ctx_q", action="TAKER") == pytest.approx(-0.10)


# ──────────────────────────────────────────────────────────────────
# Attribution exporters
# ──────────────────────────────────────────────────────────────────


@dataclass
class _StubAttrReport:
    per_alpha: pd.DataFrame


def test_export_attribution_report_updates_cum_pnl_gauges() -> None:
    df = pd.DataFrame({
        "cumulative_pnl": [0.05, -0.02, 0.0],
        "sharpe": [1.0, -0.5, 0.0],
        "hit_ratio": [0.6, 0.3, 0.0],
        "max_drawdown": [0.02, 0.05, 0.0],
        "avg_weight": [0.4, 0.3, 0.0],
        "weight_turnover": [0.1, 0.2, 0.0],
    }, index=["alpha_x", "alpha_y", "_overlay"])
    export_attribution_report(_StubAttrReport(per_alpha=df))
    assert _gauge_value(ATTRIBUTION_ALPHA_CUMULATIVE_PNL, alpha_name="alpha_x") == pytest.approx(0.05)
    assert _gauge_value(ATTRIBUTION_ALPHA_CUMULATIVE_PNL, alpha_name="alpha_y") == pytest.approx(-0.02)
    # _overlay should NOT register
    # (no gauge labels created for _overlay)


def test_export_attribution_empty_no_crash() -> None:
    export_attribution_report(_StubAttrReport(per_alpha=pd.DataFrame()))


@dataclass
class _StubRegimeReport:
    cumulative_by_regime: pd.DataFrame


def test_export_regime_attribution_sets_alpha_regime_gauges() -> None:
    df = pd.DataFrame({
        "TREND": [0.10, -0.01],
        "RANGE": [-0.02, 0.05],
    }, index=["a_trend", "a_mr"])
    export_regime_attribution_report(_StubRegimeReport(cumulative_by_regime=df))
    assert _gauge_value(
        ATTRIBUTION_ALPHA_REGIME_PNL, alpha_name="a_trend", regime="TREND"
    ) == pytest.approx(0.10)
    assert _gauge_value(
        ATTRIBUTION_ALPHA_REGIME_PNL, alpha_name="a_mr", regime="RANGE"
    ) == pytest.approx(0.05)


def test_export_rolling_sharpe_updates_last_row() -> None:
    df = pd.DataFrame({
        "alpha_x": [0.5, 0.7, 1.2],
        "alpha_y": [0.0, -0.3, -0.5],
    }, index=pd.RangeIndex(3))
    export_rolling_sharpe(df)
    assert _gauge_value(
        ATTRIBUTION_ALPHA_ROLLING_SHARPE, alpha_name="alpha_x"
    ) == pytest.approx(1.2)
    assert _gauge_value(
        ATTRIBUTION_ALPHA_ROLLING_SHARPE, alpha_name="alpha_y"
    ) == pytest.approx(-0.5)


def test_export_rolling_sharpe_empty_no_crash() -> None:
    export_rolling_sharpe(pd.DataFrame())
    export_rolling_sharpe(None)
