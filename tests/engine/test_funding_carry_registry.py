"""Tests for funding_carry registration in the alpha registry + config wiring."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from shared.alpha.base import AlphaConfig
from shared.alpha.funding_carry import FundingCarryAlpha
from shared.alpha.registry import (
    ALPHA_REGISTRY,
    PRODUCTION_READY_ALPHAS,
    get_alpha,
)
from shared.engine.config import alphas_for_symbol, load_config


def test_funding_carry_in_registry():
    assert "funding_carry" in ALPHA_REGISTRY


def test_funding_carry_in_production_ready():
    # v4.4 promotion (2026-04-24): OOS avg Δ +0.55 ensemble Sharpe
    assert "funding_carry" in PRODUCTION_READY_ALPHAS


def test_get_alpha_with_symbol_kwarg():
    cfg = AlphaConfig(name="funding_carry", params={"z_window": 360, "dead_zone": 1.0})
    alpha = get_alpha("funding_carry", cfg, symbol="ETHUSDT")
    assert isinstance(alpha, FundingCarryAlpha)
    assert alpha._symbol == "ETHUSDT"


def test_get_alpha_without_symbol_still_works():
    # Non-symbol-aware alphas should not break
    cfg = AlphaConfig(name="momentum_ensemble")
    alpha = get_alpha("momentum_ensemble", cfg)
    assert alpha is not None


def test_production_config_includes_funding_carry_per_active_symbol():
    cfg = load_config("config/v4_production.json")
    assert "funding_carry" in cfg.alphas

    # Active symbols (BTC + ETH as of 2026-05-04 universe optimization) must
    # carry funding_carry in their override list. BNB and SOL were parked
    # (data/results/universe_decision_2026-05-04.md) — their override entries
    # remain wired for unparking but the symbols themselves are inactive.
    for sym in ("BTCUSDT", "ETHUSDT"):
        names, params = alphas_for_symbol(cfg, sym)
        assert "funding_carry" in names, f"{sym} missing funding_carry"
        sym_params = params["funding_carry"]
        assert "z_window" in sym_params
        assert "dead_zone" in sym_params
        assert "scale" in sym_params

    # All parked symbols (no-edge + universe-decision parks) return [].
    for sym in ("XRPUSDT", "DOGEUSDT", "BNBUSDT", "SOLUSDT"):
        names, _ = alphas_for_symbol(cfg, sym)
        assert names == [], f"{sym} should be parked (no alphas)"


def test_funding_carry_per_symbol_params_differ_for_active():
    """Each active symbol has its own sweep-optimal params."""
    cfg = load_config("config/v4_production.json")
    btc_p = alphas_for_symbol(cfg, "BTCUSDT")[1]["funding_carry"]
    eth_p = alphas_for_symbol(cfg, "ETHUSDT")[1]["funding_carry"]
    # Per sweep results (2026-04-24): BTC uses longer window, ETH shorter
    assert btc_p["z_window"] != eth_p["z_window"]


def test_parked_overrides_still_have_funding_carry_params():
    """When BNB/SOL get unparked their funding_carry params should already be
    wired so we don't lose the per-symbol tuning. asset_overrides survives
    the symbols_parked move (intentional)."""
    cfg = load_config("config/v4_production.json")
    bnb_override = cfg.asset_overrides.get("BNBUSDT", {})
    sol_override = cfg.asset_overrides.get("SOLUSDT", {})
    assert bnb_override.get("params", {}).get("funding_carry"), \
        "BNBUSDT override lost funding_carry params — unparking would default-revert"
    assert sol_override.get("params", {}).get("funding_carry"), \
        "SOLUSDT override lost funding_carry params"


def test_funding_carry_generates_series_for_known_symbol():
    """Smoke test: funding_carry should produce a valid position series."""
    # Build synthetic OHLCV covering 2020+
    idx = pd.date_range("2020-01-01", "2022-12-31", freq="1h", tz="UTC")
    df = pd.DataFrame({
        "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.0, "volume": 1000.0,
    }, index=idx)
    alpha = FundingCarryAlpha(
        config=AlphaConfig(name="funding_carry", params={"z_window": 720, "dead_zone": 1.0}),
        symbol="BTCUSDT",  # BTCUSDT_funding.csv exists in data/funding
    )
    sig = alpha.generate(df)
    assert sig.position is not None
    assert len(sig.position) == len(df)
    # Positions should be bounded by tanh() → [-1, 1]
    assert (sig.position.abs() <= 1.0 + 1e-9).all()
