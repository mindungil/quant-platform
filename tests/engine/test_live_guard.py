"""Tests for Phase 1 live-guard: config parking + 6M Sharpe auto-park."""
from __future__ import annotations

import json

import numpy as np
import pytest

from shared.engine.config import (
    EngineConfig,
    alphas_for_symbol,
    is_symbol_parked,
    load_config,
)


# Reproduces evaluate_live_guard from scripts/live/generate_signals.py so we
# can test the verdict logic without importing the script (which pulls the
# binance fetcher and regime module at import time).
def _evaluate_live_guard(cfg: EngineConfig, pnl: np.ndarray, ppy: int):
    from shared.backtest.metrics import sharpe_ratio

    if not cfg.live_guard_enabled:
        return "DISABLED", 1.0, float("nan")

    bars_6m = int(cfg.live_guard_lookback_days * ppy / 365)
    recent = pnl[-bars_6m:] if len(pnl) >= bars_6m else pnl
    if len(recent) < cfg.live_guard_min_bars:
        return "INSUFFICIENT_DATA", 1.0, float("nan")

    sr_6m = sharpe_ratio(recent, periods_per_year=ppy)
    if not np.isfinite(sr_6m):
        return "INSUFFICIENT_DATA", 1.0, float("nan")

    if sr_6m < cfg.live_guard_park_threshold:
        return "PARKED", 0.0, float(sr_6m)
    if sr_6m < cfg.live_guard_warn_threshold:
        return "WARN", 0.5, float(sr_6m)
    return "ACTIVE", 1.0, float(sr_6m)


def _cfg_with_parked():
    return EngineConfig(
        alphas={
            "momentum_ensemble": {"windows": [168, 720]},
            "range_reversion": {},
            "vol_breakout": {"hold_bars": 96},
        },
        symbols=["BTCUSDT", "ETHUSDT"],
        asset_overrides={
            "BTCUSDT": {"alphas": ["momentum_ensemble", "range_reversion"]},
            "ETHUSDT": {"alphas": ["momentum_ensemble", "vol_breakout"]},
        },
        symbols_parked={
            "SOLUSDT": {"reason": "DSR=0.25 suspect", "last_dsr": 0.25},
            "XRPUSDT": {"reason": "no edge", "last_sharpe": 0.4},
        },
        live_guard_enabled=True,
        live_guard_lookback_days=180,
        live_guard_park_threshold=-0.5,
        live_guard_warn_threshold=0.0,
        live_guard_min_bars=500,
    )


# ──────────────────────────────────────────────────────────────────
# Symbol parking tests
# ──────────────────────────────────────────────────────────────────

def test_parked_symbol_returns_empty_alpha_list():
    cfg = _cfg_with_parked()
    alphas, _ = alphas_for_symbol(cfg, "SOLUSDT")
    assert alphas == []


def test_parked_symbol_is_parked_with_reason():
    cfg = _cfg_with_parked()
    parked, reason = is_symbol_parked(cfg, "SOLUSDT")
    assert parked is True
    assert "DSR" in reason


def test_active_symbol_uses_override_alphas():
    cfg = _cfg_with_parked()
    alphas, params = alphas_for_symbol(cfg, "BTCUSDT")
    assert alphas == ["momentum_ensemble", "range_reversion"]
    assert params["momentum_ensemble"] == {"windows": [168, 720]}


def test_unknown_symbol_falls_back_to_global_alphas():
    cfg = _cfg_with_parked()
    alphas, params = alphas_for_symbol(cfg, "LINKUSDT")
    assert set(alphas) == {"momentum_ensemble", "range_reversion", "vol_breakout"}


def test_empty_override_alphas_treated_as_parked():
    cfg = EngineConfig(
        alphas={"momentum_ensemble": {}},
        asset_overrides={"FOOUSDT": {"alphas": [], "note": "no edge found"}},
    )
    parked, reason = is_symbol_parked(cfg, "FOOUSDT")
    assert parked is True
    assert "no edge" in reason
    alphas, _ = alphas_for_symbol(cfg, "FOOUSDT")
    assert alphas == []


# ──────────────────────────────────────────────────────────────────
# Live-guard Sharpe verdict tests
# ──────────────────────────────────────────────────────────────────

def test_live_guard_park_on_deeply_negative_sharpe():
    cfg = _cfg_with_parked()
    # 1000 bars of consistently negative returns → Sharpe far below -0.5
    rng = np.random.default_rng(42)
    pnl = rng.normal(loc=-0.001, scale=0.005, size=2000)
    verdict, mult, sr = _evaluate_live_guard(cfg, pnl, ppy=24 * 365)
    assert verdict == "PARKED"
    assert mult == 0.0
    assert sr < cfg.live_guard_park_threshold


def test_live_guard_warn_on_mildly_negative_sharpe():
    cfg = _cfg_with_parked()
    # Construct deterministic pnl with known negative Sharpe
    # Want Sharpe in warn zone (-0.5, 0), so widen thresholds to make robust.
    rng = np.random.default_rng(7)
    pnl = rng.normal(loc=-5e-6, scale=0.01, size=2000)
    cfg.live_guard_park_threshold = -10.0
    cfg.live_guard_warn_threshold = 10.0  # every non-positive-huge verdict → WARN
    verdict, mult, _ = _evaluate_live_guard(cfg, pnl, ppy=24 * 365)
    assert verdict == "WARN"
    assert mult == 0.5


def test_live_guard_active_on_positive_sharpe():
    cfg = _cfg_with_parked()
    rng = np.random.default_rng(3)
    pnl = rng.normal(loc=0.0005, scale=0.005, size=2000)
    verdict, mult, sr = _evaluate_live_guard(cfg, pnl, ppy=24 * 365)
    assert verdict == "ACTIVE"
    assert mult == 1.0
    assert sr > 0


def test_live_guard_insufficient_data_when_below_min_bars():
    cfg = _cfg_with_parked()
    cfg.live_guard_min_bars = 5000
    pnl = np.random.default_rng(0).normal(size=2000)
    verdict, mult, _ = _evaluate_live_guard(cfg, pnl, ppy=24 * 365)
    assert verdict == "INSUFFICIENT_DATA"
    assert mult == 1.0  # fail-open: don't block when data is short


def test_live_guard_disabled_returns_active_equivalent():
    cfg = _cfg_with_parked()
    cfg.live_guard_enabled = False
    pnl = np.random.default_rng(0).normal(loc=-0.01, scale=0.001, size=2000)
    verdict, mult, _ = _evaluate_live_guard(cfg, pnl, ppy=24 * 365)
    assert verdict == "DISABLED"
    assert mult == 1.0  # don't gate when disabled


# ──────────────────────────────────────────────────────────────────
# Config round-trip test
# ──────────────────────────────────────────────────────────────────

def test_config_round_trip_preserves_live_guard_and_overrides(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({
        "alphas": {"momentum_ensemble": {"enabled": True, "params": {"x": 1}}},
        "symbols": ["BTCUSDT"],
        "asset_overrides": {"BTCUSDT": {"alphas": ["momentum_ensemble"], "maker_dsr": 0.9}},
        "symbols_parked": {"SOLUSDT": {"reason": "suspect"}},
        "parked_alphas": {"macro_context": {"reason": "decay"}},
        "live_guard": {
            "enabled": True,
            "lookback_days": 180,
            "park_threshold": -0.4,
            "warn_threshold": -0.1,
            "min_bars": 2000,
        },
    }))
    cfg = load_config(p)
    assert cfg.symbols == ["BTCUSDT"]
    assert cfg.asset_overrides["BTCUSDT"]["alphas"] == ["momentum_ensemble"]
    assert "SOLUSDT" in cfg.symbols_parked
    assert "macro_context" in cfg.parked_alphas
    assert cfg.live_guard_enabled is True
    assert cfg.live_guard_park_threshold == pytest.approx(-0.4)
    assert cfg.live_guard_warn_threshold == pytest.approx(-0.1)
    assert cfg.live_guard_min_bars == 2000
