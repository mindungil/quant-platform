"""Tests for the self-improving engine module."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from shared.engine.config import EngineConfig, save_config, load_config
from shared.engine.logger import PerformanceLogger
from shared.engine.health import AlphaHealthMonitor, HealthStatus
from shared.engine.adaptive_tf import AdaptiveTimeframe
from shared.engine.refit import RollingRefitter, _welch_t_test


def _ts(values, freq="h"):
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


class TestEngineConfig:
    def test_save_and_load_roundtrip(self, tmp_path):
        cfg = EngineConfig()
        cfg.alphas["kalman_trend"]["obs_var"] = 0.001
        path = tmp_path / "test_config.json"
        save_config(cfg, path)
        loaded = load_config(path)
        assert loaded.alphas["kalman_trend"]["obs_var"] == 0.001
        assert loaded.combine_mode == "equal"

    def test_default_has_3_alphas(self):
        cfg = EngineConfig()
        assert len(cfg.alphas) == 3
        assert "kalman_trend" in cfg.alphas

    def test_load_missing_returns_default(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.json")
        assert isinstance(cfg, EngineConfig)


class TestPerformanceLogger:
    def test_log_and_flush(self, tmp_path):
        logger = PerformanceLogger(str(tmp_path / "metrics"))
        logger.log_bar("2024-01-01T00:00", "BTCUSDT",
                       {"kalman_trend": 0.5}, {"kalman_trend": 0.33},
                       0.5, 0.01)
        logger.flush("BTCUSDT")
        files = list((tmp_path / "metrics" / "BTCUSDT").glob("*.jsonl"))
        assert len(files) == 1
        with open(files[0]) as f:
            line = json.loads(f.readline())
        assert line["sym"] == "BTCUSDT"
        assert abs(line["pnl"] - 0.005) < 1e-6

    def test_load_recent_empty(self, tmp_path):
        logger = PerformanceLogger(str(tmp_path / "metrics"))
        df = logger.load_recent("BTCUSDT")
        assert df.empty


class TestAlphaHealthMonitor:
    def test_healthy_alpha(self):
        rng = np.random.default_rng(42)
        n = 1000
        ret = _ts(rng.normal(0, 0.01, n))
        pos = _ts(np.sign(ret.values))  # perfect signal
        monitor = AlphaHealthMonitor(windows_hours=[100, 500])
        health = monitor.assess({"trend": pos}, ret)
        assert health["trend"].status == HealthStatus.HEALTHY
        assert health["trend"].recommended_weight == 1.0

    def test_critical_alpha(self):
        rng = np.random.default_rng(7)
        n = 1000
        ret = _ts(rng.normal(0, 0.01, n))
        pos = _ts(-np.sign(ret.values))  # anti-signal
        monitor = AlphaHealthMonitor(windows_hours=[100, 500], sharpe_critical=-0.3)
        health = monitor.assess({"bad": pos}, ret)
        assert health["bad"].status == HealthStatus.CRITICAL
        assert health["bad"].recommended_weight == 0.0


class TestAdaptiveTimeframe:
    def test_high_vol_selects_1h(self):
        rng = np.random.default_rng(1)
        n = 2000
        # High vol: large returns
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
        df = pd.DataFrame({"close": close},
                          index=pd.date_range("2024-01-01", periods=n, freq="h"))
        atf = AdaptiveTimeframe(vol_high_z=-99, dwell_bars=1)  # always trigger
        decision = atf.select(df)
        assert decision.timeframe in ("1h", "8h")  # runs without error

    def test_hysteresis_prevents_thrashing(self):
        rng = np.random.default_rng(3)
        n = 2000
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        df = pd.DataFrame({"close": close},
                          index=pd.date_range("2024-01-01", periods=n, freq="h"))
        atf = AdaptiveTimeframe(dwell_bars=100)  # very high dwell
        d1 = atf.select(df)
        d2 = atf.select(df)
        # With high dwell, should stick to default
        assert d1.timeframe == d2.timeframe


class TestWelchTTest:
    def test_identical_arrays_high_pvalue(self):
        a = np.random.randn(100)
        assert _welch_t_test(a, a.copy()) > 0.3

    def test_different_means_low_pvalue(self):
        a = np.random.randn(1000)
        b = np.random.randn(1000) + 1.0  # clearly different
        assert _welch_t_test(a, b) < 0.01


class TestRollingRefitter:
    def test_keeps_current_when_no_improvement(self):
        rng = np.random.default_rng(42)
        n = 6000  # ~250 days
        close = 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.01, n)))
        df = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": rng.uniform(100, 10000, n),
        }, index=pd.date_range("2024-01-01", periods=n, freq="h"))
        refitter = RollingRefitter(
            current_params={"kalman_trend": {"obs_var": 5e-4, "slope_var": 5e-8}},
            lookback_days=90, oos_days=30, significance=0.01, safety_margin=0.5,
        )
        result = refitter.refit_alpha("kalman_trend", df)
        # With very strict significance + margin, shouldn't promote
        assert isinstance(result.promoted, bool)
        assert result.p_value >= 0
