"""Tests for AlphaMiner pipeline."""
import numpy as np
import pandas as pd
import pytest

from shared.engine.alpha_miner import (
    AlphaMiner,
    AlphaMinerConfig,
    MiningResult,
    _cluster_features,
    _sample_from_groups,
)


def _make_ohlcv(n: int = 5000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.01, n)
    signal = np.zeros(n)
    for i in range(1, n):
        signal[i] = 0.2 * signal[i - 1] + noise[i]
    close = 100.0 * np.exp(np.cumsum(signal))
    high = close * (1 + rng.uniform(0, 0.015, n))
    low = close * (1 - rng.uniform(0, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.uniform(100, 10000, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "taker_buy_base": volume * rng.uniform(0.3, 0.7, n),
    }, index=idx)


class TestAlphaMiner:

    def test_cluster_features(self):
        """Feature clustering should group correlated features."""
        rng = np.random.default_rng(42)
        # Create corr matrix with 3 clear groups
        n = 10
        corr = np.eye(n)
        for i in range(0, 3):
            for j in range(0, 3):
                corr[i, j] = 0.9
        for i in range(3, 6):
            for j in range(3, 6):
                corr[i, j] = 0.85
        groups = _cluster_features(corr, threshold=0.7)
        assert len(groups) >= 3  # at least 3 distinct groups

    def test_sample_from_groups(self):
        """Sampling should pick from diverse groups."""
        groups = [[0, 1, 2], [3, 4], [5, 6, 7], [8, 9]]
        rng = np.random.default_rng(42)
        selected = _sample_from_groups(groups, 10, 6, rng)
        assert len(selected) == 6
        assert len(set(selected)) == 6  # no duplicates

    def test_miner_smoke(self):
        """Mining should complete without errors."""
        dfs = {
            "BTC": _make_ohlcv(seed=1),
            "ETH": _make_ohlcv(seed=2),
            "BNB": _make_ohlcv(seed=3),
        }
        config = AlphaMinerConfig(
            n_candidates=3,  # keep fast for test
            features_per_model=15,
            train_window=2000,
            refit_every=500,
            models_dir="/tmp/test_mining_models",
            trials_path="/tmp/test_mining_trials.json",
            log_dir="/tmp/test_mining_log",
        )
        miner = AlphaMiner(config)
        result = miner.mine(dfs)
        assert isinstance(result, MiningResult)
        assert result.n_candidates_tested == 3
        assert result.cumulative_trials >= 3

    def test_miner_returns_valid_candidates(self):
        """Any returned candidates should have valid metrics."""
        dfs = {
            "BTC": _make_ohlcv(seed=10),
            "ETH": _make_ohlcv(seed=20),
            "BNB": _make_ohlcv(seed=30),
        }
        config = AlphaMinerConfig(
            n_candidates=5,
            features_per_model=20,
            min_oos_sharpe=-999,  # accept everything for this test
            max_corr_existing=999,
            min_symbols_positive=0,
            max_drawdown=999,
            train_window=2000,
            refit_every=500,
            models_dir="/tmp/test_mining_models2",
            trials_path="/tmp/test_mining_trials2.json",
            log_dir="/tmp/test_mining_log2",
        )
        miner = AlphaMiner(config)
        result = miner.mine(dfs)
        for c in result.all_candidates:
            assert len(c.oos_sharpes) > 0
            assert len(c.feature_names) > 0
            assert np.isfinite(c.avg_oos_sharpe)
            assert c.max_drawdown >= 0

    def test_cumulative_trials_persist(self):
        """Trial count should accumulate across runs."""
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        trials_path = os.path.join(tmpdir, "trials.json")

        dfs = {"BTC": _make_ohlcv(n=5000, seed=42)}
        config = AlphaMinerConfig(
            n_candidates=2,
            features_per_model=10,
            train_window=2000,
            refit_every=500,
            trials_path=trials_path,
            log_dir=os.path.join(tmpdir, "log"),
            models_dir=os.path.join(tmpdir, "models"),
        )

        miner = AlphaMiner(config)
        r1 = miner.mine(dfs)
        assert r1.cumulative_trials == 2

        r2 = miner.mine(dfs)
        assert r2.cumulative_trials == 4
