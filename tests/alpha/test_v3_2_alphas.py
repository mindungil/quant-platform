"""Tests for v3.2 alpha additions: OnlineRLS + cross-asset features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha import MetaForestAlpha, OnlineRLSAlpha, get_alpha
from shared.alpha.ml_forest import _build_rich_features
from shared.backtest.synthetic import generate_synthetic_ohlcv


@pytest.fixture
def df():
    return generate_synthetic_ohlcv(n_bars=2500, seed=7, trend_strength=8.0)


@pytest.fixture
def btc_exog():
    return generate_synthetic_ohlcv(n_bars=2500, seed=11, trend_strength=6.0)


def test_online_rls_position_bounded(df):
    sig = OnlineRLSAlpha().generate(df)
    assert sig.position.between(-1.0, 1.0).all()
    assert len(sig.position) == len(df)


def test_online_rls_warmup_zeros(df):
    sig = OnlineRLSAlpha().generate(df)
    assert (sig.position.iloc[:500].abs() < 1e-9).all()


def test_online_rls_in_registry():
    a = get_alpha("online_rls")
    assert a.name == "online_rls"


def test_rich_features_with_exog_adds_columns(df, btc_exog):
    f1 = _build_rich_features(df)
    f2 = _build_rich_features(df, exog=btc_exog)
    assert f2.shape[1] > f1.shape[1]
    # Cross-asset feature names should be present
    cross_keys = {"btc_ret_24", "btc_ret_72", "btc_vol_24", "btc_corr_168", "btc_vol_ratio"}
    assert cross_keys.issubset(set(f2.columns))


def test_meta_forest_with_exog_runs(df, btc_exog):
    sig = MetaForestAlpha(exog=btc_exog).generate(df)
    assert sig.position.between(-1.0, 1.0).all()
    assert len(sig.position) == len(df)


def test_mean_reversion_strict_gate_reduces_position():
    from shared.alpha import MeanReversionAlpha
    from shared.alpha.base import AlphaConfig
    df = generate_synthetic_ohlcv(n_bars=2500, seed=7, trend_strength=8.0)
    open_gate = MeanReversionAlpha(
        AlphaConfig(name="mean_reversion", params={"use_strict_gate": False})
    ).generate(df)
    closed_gate = MeanReversionAlpha(
        AlphaConfig(name="mean_reversion", params={"use_strict_gate": True})
    ).generate(df)
    # Closed gate should have ≤ active bars than open gate
    open_active = float((open_gate.position.abs() > 0.01).mean())
    closed_active = float((closed_gate.position.abs() > 0.01).mean())
    assert closed_active <= open_active
