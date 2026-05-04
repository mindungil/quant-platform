"""Unit tests for scripts/live/stress_monitor.py thresholds + window transitions."""

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/live/stress_monitor.py"


@pytest.fixture
def stress_module():
    spec = importlib.util.spec_from_file_location("stress_monitor", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_btc_df(daily_ret: float = 0.0, peak_drop: float = 0.0):
    """Build a minimal 25-row hourly DataFrame for BTC checks.

    daily_ret: 24h close-to-close return (last close is 100*(1+daily_ret))
    peak_drop: drop from 24h peak to last close (positive value, e.g. 0.04 for -4%)
    """
    rows = 25
    base = 100.0
    closes = [base] * rows
    highs = [base] * rows
    closes[-1] = base * (1.0 + daily_ret)
    if peak_drop > 0:
        # Inject a peak in the middle: peak = last / (1 - peak_drop)
        peak = closes[-1] / (1.0 - peak_drop)
        highs[-12] = peak
    df = pd.DataFrame({"close": closes, "high": highs,
                       "open": closes, "low": closes, "volume": [1000] * rows})
    df.index = pd.date_range("2026-04-25", periods=rows, freq="1h", tz="UTC")
    return df


def test_btc_daily_return_below_threshold_no_fire(stress_module):
    df = _make_btc_df(daily_ret=0.02)  # 2% — under 5% threshold
    with patch.object(stress_module, "load_ohlcv_stitched", return_value=df):
        fired, det = stress_module.check_btc_daily_return(0.05)
    assert not fired
    assert abs(det["ret"] - 0.02) < 1e-6


def test_btc_daily_return_above_threshold_fires(stress_module):
    df = _make_btc_df(daily_ret=-0.06)  # -6% — over ±5%
    with patch.object(stress_module, "load_ohlcv_stitched", return_value=df):
        fired, det = stress_module.check_btc_daily_return(0.05)
    assert fired
    assert det["ret"] < -0.05


def test_btc_1h_dd_below_threshold_no_fire(stress_module):
    df = _make_btc_df(daily_ret=0.0, peak_drop=0.01)  # 1% drawdown — under 3%
    with patch.object(stress_module, "load_ohlcv_stitched", return_value=df):
        fired, det = stress_module.check_btc_1h_drawdown_from_peak(0.03)
    assert not fired


def test_btc_1h_dd_above_threshold_fires(stress_module):
    df = _make_btc_df(daily_ret=0.0, peak_drop=0.05)  # 5% drawdown
    with patch.object(stress_module, "load_ohlcv_stitched", return_value=df):
        fired, det = stress_module.check_btc_1h_drawdown_from_peak(0.03)
    assert fired
    assert det["dd_from_24h_peak"] < -0.03


def test_btc_no_data_returns_false(stress_module):
    with patch.object(stress_module, "load_ohlcv_stitched",
                      side_effect=FileNotFoundError("no csv")):
        fired, det = stress_module.check_btc_daily_return(0.05)
    assert not fired
    assert det["reason"] == "no_data"


def test_paper_dd_jump_fires_on_increase(stress_module, tmp_path, monkeypatch):
    paper_path = tmp_path / "portfolio_state.json"
    paper_path.write_text(json.dumps({"max_drawdown": 0.05}))
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"stress_window": {"last_paper_dd": 0.02}}))
    monkeypatch.setattr(stress_module, "PAPER_STATE", paper_path)
    monkeypatch.setattr(stress_module, "STATE_PATH", state_path)
    fired, det = stress_module.check_paper_dd_jump(0.02)
    assert fired
    assert abs(det["delta"] - 0.03) < 1e-6


def test_paper_dd_jump_no_fire_on_small_change(stress_module, tmp_path, monkeypatch):
    paper_path = tmp_path / "portfolio_state.json"
    paper_path.write_text(json.dumps({"max_drawdown": 0.05}))
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"stress_window": {"last_paper_dd": 0.045}}))
    monkeypatch.setattr(stress_module, "PAPER_STATE", paper_path)
    monkeypatch.setattr(stress_module, "STATE_PATH", state_path)
    fired, det = stress_module.check_paper_dd_jump(0.02)
    assert not fired
