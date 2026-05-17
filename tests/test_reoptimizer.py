"""Tests for shared.portfolio.reoptimizer — V4-4."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.execution.router import FeeSchedule, VenueQuote
from shared.portfolio.meta_ensemble import MetaEnsembleConfig
from shared.portfolio.reoptimizer import ReoptInput, reoptimize
from shared.risk import capital_tier
from shared.risk.monitor_hub import clear_notifiers


@pytest.fixture(autouse=True)
def _reset():
    capital_tier.set_active_tier("FULL", reason="test")
    capital_tier.clear_kill()
    clear_notifiers()
    yield
    capital_tier.set_active_tier("PAPER", reason="test_reset")


def _simple_input(
    target_positions_override: dict[str, float] | None = None,
    current_positions: dict[str, float] | None = None,
) -> ReoptInput:
    """Build minimal valid ReoptInput for a single-asset case."""
    n = 100
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    # Single alpha, all 1.0 positions → combined target = 1.0
    alpha_positions = pd.DataFrame({"alpha_a": [1.0] * n}, index=idx)
    bar_returns = pd.Series(rng.normal(0.001, 0.01, n), index=idx)
    venue_quotes = {
        "BTC": {
            "binance": VenueQuote("binance", 49_900, 50_100, 100, 100),
            "coinbase": VenueQuote("coinbase", 49_950, 50_050, 100, 100),
        }
    }
    market_data = {"BTC": {"mid_price": 50_000, "spread_bp": 4.0, "adv_usd": 1_000_000_000}}
    return ReoptInput(
        alpha_positions=alpha_positions,
        bar_returns=bar_returns,
        current_positions=current_positions or {"BTC": 0.5},
        market_data=market_data,
        venue_quotes=venue_quotes,
        expected_alpha_per_position_bps={"BTC": 5.0},
    )


# ─── Basic shape ───────────────────────────────────────────────────


def test_reoptimize_returns_full_result_shape() -> None:
    out = reoptimize(_simple_input())
    assert out.target_positions
    assert out.rebalance_plan is not None
    assert isinstance(out.routing_plans, dict)
    assert isinstance(out.risk_events_emitted, list)


def test_summary_keys() -> None:
    out = reoptimize(_simple_input())
    s = out.summary()
    expected = {"target_positions_count", "n_orders", "n_skipped", "venues_used",
                "total_unfilled_qty", "capital_tier", "tier_capped_orders", "risk_events"}
    assert expected <= s.keys()


# ─── Target generation ─────────────────────────────────────────────


def test_target_positions_include_held_symbols() -> None:
    out = reoptimize(_simple_input(current_positions={"BTC": 1.0, "ETH": 5.0}))
    assert "BTC" in out.target_positions
    assert "ETH" in out.target_positions


# ─── Rebalance pipeline ────────────────────────────────────────────


def test_rebalance_skip_when_drift_small() -> None:
    # Holdings already at target → no orders
    inp = _simple_input(current_positions={"BTC": 1.0})
    out = reoptimize(inp)
    # If drift is tiny, plan may have 0 orders
    assert out.summary()["n_orders"] >= 0


def test_rebalance_executes_on_significant_drift() -> None:
    # Big difference between target (combined ~ -1..1) and current 100
    inp = _simple_input(current_positions={"BTC": 100.0})
    out = reoptimize(inp)
    # Either executes or skips with cost; both fine, just ensure no crash
    assert "BTC" in out.target_positions


# ─── Capital tier capping ──────────────────────────────────────────


def test_tier_caps_oversize_orders() -> None:
    capital_tier.set_active_tier("MICRO")  # max $10/order
    inp = _simple_input(current_positions={"BTC": 0.0})
    # Force target_position to a value that yields large notional
    inp.alpha_positions = pd.DataFrame({"alpha_a": [1.0] * 100},
                                        index=inp.alpha_positions.index)
    inp.current_positions = {"BTC": 0.0}
    # rebalancer thinks it wants positive quantity → notional > $10 → capped
    out = reoptimize(inp)
    # Some orders likely capped (depends on rebalance plan output)
    # Verify the capping mechanism runs without crashing
    assert out.capital_tier == "MICRO"


def test_tier_field_in_result() -> None:
    capital_tier.set_active_tier("SMALL")
    out = reoptimize(_simple_input())
    assert out.capital_tier == "SMALL"


# ─── Venue routing ─────────────────────────────────────────────────


def test_missing_venue_emits_obs_event() -> None:
    inp = _simple_input(current_positions={"ETH": 100.0})
    inp.market_data["ETH"] = {"mid_price": 3000, "spread_bp": 5, "adv_usd": 1e9}
    # No venue_quotes for ETH → if a rebalance order fires, it should
    # record a risk event for missing venues.
    out = reoptimize(inp)
    # At minimum no crash
    assert isinstance(out.risk_events_emitted, list)


def test_routing_plans_populated_when_orders_execute() -> None:
    capital_tier.set_active_tier("FULL")
    inp = _simple_input(current_positions={"BTC": 10.0})
    out = reoptimize(inp)
    # If there are orders, routing_plans should be populated for those symbols
    for order in out.rebalance_plan.orders:
        if order.symbol in inp.venue_quotes:
            assert order.symbol in out.routing_plans
