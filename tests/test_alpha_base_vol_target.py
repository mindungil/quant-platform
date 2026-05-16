"""Tests for the opt-in vol-target overlay in shared.alpha.base.Alpha.generate.

Lives at the repo root (NOT under tests/alpha/) because base.py is public —
keeps this test in the public quant-platform after the open-core split.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha.base import Alpha, AlphaConfig


class _ConstantLongAlpha(Alpha):
    """Test alpha that always wants to be 100% long."""

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


def _make_ohlcv(n: int = 400, vol_scale: float = 1.0, seed: int = 0) -> pd.DataFrame:
    """Synthetic 1h OHLCV. vol_scale=1.0 gives ~realistic crypto-like vol."""
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(0.0, 0.01 * vol_scale, n)
    close = 100.0 * np.exp(np.cumsum(log_ret))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": rng.uniform(1e6, 5e6, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )


def test_auto_vol_target_off_is_pass_through() -> None:
    """Default behavior unchanged — backward compat for 28 production alphas."""
    cfg = AlphaConfig(name="test")  # auto_vol_target=False
    alpha = _ConstantLongAlpha(cfg)
    sig = alpha.generate(_make_ohlcv())
    # Raw position is 1.0, shift-1 then no scaling → still 1.0 from bar 1 onward
    assert sig.position.iloc[-1] == pytest.approx(1.0)
    assert "vol_target_applied" not in sig.diagnostics


def test_auto_vol_target_on_low_vol_scales_up_capped() -> None:
    """Low realized vol → scale wants > 1, but capped at vol_target_cap."""
    cfg = AlphaConfig(
        name="test",
        auto_vol_target=True,
        target_vol=0.40,      # high target
        vol_target_cap=1.5,
        vol_target_lookback=72,
    )
    alpha = _ConstantLongAlpha(cfg)
    sig = alpha.generate(_make_ohlcv(vol_scale=0.3))  # low realized vol
    # Cap should bind → position ≤ 1.5 but max_gross_position default is 1.0
    # so final position is clipped to 1.0
    assert sig.diagnostics["vol_target_applied"] is True
    assert sig.diagnostics["vol_scale_last"] is not None
    assert sig.diagnostics["vol_scale_last"] > 1.0  # wanted to scale up


def test_auto_vol_target_on_high_vol_scales_down() -> None:
    """High realized vol → scale < 1 → position dampened below 1.0."""
    cfg = AlphaConfig(
        name="test",
        auto_vol_target=True,
        target_vol=0.15,  # 15% annual target
        vol_target_lookback=72,
        vol_target_floor=0.0,
    )
    alpha = _ConstantLongAlpha(cfg)
    sig = alpha.generate(_make_ohlcv(vol_scale=3.0))  # 3x normal vol
    assert sig.diagnostics["vol_target_applied"] is True
    # Realized vol should massively exceed target → scale << 1
    assert sig.diagnostics["vol_scale_last"] < 0.5
    # Position should be well below 1.0 after scaling
    assert abs(sig.position.iloc[-1]) < 0.5


def test_auto_vol_target_panel_input_is_no_op() -> None:
    """Panel (dict) input → vol-target skipped, no crash. Ensemble handles vol."""

    class _PanelAlpha(Alpha):
        requires_panel = True

        def _generate(self, df: dict) -> pd.Series:
            any_frame = next(iter(df.values()))
            return pd.Series(0.5, index=any_frame.index)

    cfg = AlphaConfig(name="panel", auto_vol_target=True)
    panel = {"BTCUSDT": _make_ohlcv(), "ETHUSDT": _make_ohlcv(seed=1)}
    sig = _PanelAlpha(cfg).generate(panel)
    # No scaling on panels — position should equal shifted raw (0.5)
    assert sig.position.iloc[-1] == pytest.approx(0.5)
    # Diagnostics still include the flag because auto_vol_target is True
    assert sig.diagnostics["vol_target_applied"] is True
    assert sig.diagnostics["vol_scale_last"] is None  # never computed


def test_auto_vol_target_missing_close_column_is_no_op() -> None:
    """Frame without 'close' → vol-target skipped, no crash."""
    cfg = AlphaConfig(name="x", auto_vol_target=True)

    class _CheaterAlpha(Alpha):
        def _generate(self, df: pd.DataFrame) -> pd.Series:
            return pd.Series(0.3, index=df.index)

    df = pd.DataFrame({"price": np.arange(100.0)}, index=pd.RangeIndex(100))
    sig = _CheaterAlpha(cfg).generate(df)
    # Shift-1 of constant 0.3 → 0.3 from bar 1 onward, no scaling
    assert sig.position.iloc[-1] == pytest.approx(0.3)


def test_auto_vol_target_respects_max_gross_position() -> None:
    """Even with strong scale-up, final |position| ≤ max_gross_position."""
    cfg = AlphaConfig(
        name="test",
        auto_vol_target=True,
        target_vol=0.50,
        vol_target_cap=3.0,
        max_gross_position=0.7,
    )
    alpha = _ConstantLongAlpha(cfg)
    sig = alpha.generate(_make_ohlcv(vol_scale=0.2))
    assert sig.position.abs().max() <= 0.7 + 1e-9


def test_auto_vol_target_floor_clamps_to_zero() -> None:
    """High vol + floor=0 → scale can go to 0, dampening completely."""
    cfg = AlphaConfig(
        name="test",
        auto_vol_target=True,
        target_vol=0.05,  # very low target
        vol_target_floor=0.0,
        vol_target_lookback=48,
    )
    alpha = _ConstantLongAlpha(cfg)
    sig = alpha.generate(_make_ohlcv(vol_scale=5.0, seed=7))
    assert sig.diagnostics["vol_scale_last"] >= 0.0  # never negative
    assert sig.diagnostics["vol_scale_last"] < 0.1   # heavily dampened
