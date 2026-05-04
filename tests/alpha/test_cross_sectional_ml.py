"""Tests for CrossSectionalMLAlpha."""
import numpy as np
import pandas as pd
import pytest

from shared.alpha.base import AlphaConfig
from shared.alpha.cross_sectional_ml import CrossSectionalMLAlpha


def _make_panel(n: int = 5000, n_syms: int = 5) -> dict[str, pd.DataFrame]:
    """Generate synthetic panel data."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2020-01-01", periods=n, freq="h")
    panel = {}
    for i in range(n_syms):
        noise = rng.normal(0, 0.01, n)
        close = 100.0 * (1 + i * 0.1) * np.exp(np.cumsum(noise))
        high = close * (1 + rng.uniform(0, 0.015, n))
        low = close * (1 - rng.uniform(0, 0.015, n))
        open_ = close * (1 + rng.normal(0, 0.003, n))
        volume = rng.uniform(100, 10000, n)
        panel[f"SYM{i}"] = pd.DataFrame({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume,
        }, index=idx)
    return panel


class TestCrossSectionalML:

    def test_smoke(self):
        panel = _make_panel()
        alpha = CrossSectionalMLAlpha()
        per_asset = alpha.generate_per_asset(panel)
        assert len(per_asset) == 5
        for s, pos in per_asset.items():
            assert len(pos) == len(panel[s])
            assert pos.abs().max() <= 1.0

    def test_dollar_neutral(self):
        """Sum of positions across symbols should be near zero."""
        panel = _make_panel()
        alpha = CrossSectionalMLAlpha()
        per_asset = alpha.generate_per_asset(panel)
        # After warmup, positions should be approximately dollar-neutral
        all_pos = pd.DataFrame(per_asset)
        net = all_pos.iloc[4000:].sum(axis=1).abs()
        # Not exactly zero due to tanh, but should be low
        assert net.mean() < 2.0  # generous tolerance

    def test_requires_dict(self):
        """Single-asset df → returns zero series + warning (not TypeError).

        Earlier versions raised; current impl prefers safe-zero to avoid
        catastrophic mis-trading when a cross-sectional alpha is called
        with a single-asset frame by mistake.
        """
        df = pd.DataFrame({"close": [1, 2, 3]})
        alpha = CrossSectionalMLAlpha()
        sig = alpha.generate(df)
        assert (sig.position == 0).all()

    def test_few_symbols_graceful(self):
        """Should handle < 3 symbols gracefully."""
        panel = _make_panel(n_syms=2)
        alpha = CrossSectionalMLAlpha()
        per_asset = alpha.generate_per_asset(panel)
        assert len(per_asset) == 2
        # Should return zeros
        for pos in per_asset.values():
            assert pos.abs().sum() == 0

    def test_aggregate_signal(self):
        """The aggregate generate() should return a single series."""
        panel = _make_panel()
        alpha = CrossSectionalMLAlpha()
        sig = alpha.generate(panel)
        assert isinstance(sig.position, pd.Series)
        assert len(sig.position) == len(panel["SYM0"])
