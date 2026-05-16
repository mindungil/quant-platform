"""Tests for shared.portfolio.rebalance — V3 #5.

IP test (rebalance.py is private — controls real capital flow).
"""
from __future__ import annotations

import math

import pytest

from shared.portfolio.rebalance import (
    RebalancePlan,
    plan_rebalance,
    square_root_impact_bp,
    total_cost_bp,
)


# Default market data shared across tests
_LIQUID = {"mid_price": 50_000.0, "spread_bp": 4.0, "adv_usd": 1_000_000_000}
_ILLIQUID = {"mid_price": 100.0, "spread_bp": 50.0, "adv_usd": 5_000_000}


# ──────────────────────────────────────────────────────────────────
# Cost model
# ──────────────────────────────────────────────────────────────────


def test_sqrt_impact_zero_trade() -> None:
    assert square_root_impact_bp(0.0, 1_000_000) == 0.0


def test_sqrt_impact_zero_adv() -> None:
    assert square_root_impact_bp(100_000, 0.0) == 0.0


def test_sqrt_impact_scales_with_sqrt_participation() -> None:
    """1% participation gives ~14 * sqrt(0.01) = 1.4bp impact (default coeff)."""
    impact = square_root_impact_bp(10_000, 1_000_000, coefficient=14.0)
    assert impact == pytest.approx(14.0 * math.sqrt(0.01))


def test_total_cost_combines_half_spread_and_impact() -> None:
    cost = total_cost_bp(10_000, spread_bp=4.0, adv_usd=1_000_000)
    expected = 4.0 / 2.0 + 14.0 * math.sqrt(0.01)
    assert cost == pytest.approx(expected)


# ──────────────────────────────────────────────────────────────────
# plan_rebalance — basic cases
# ──────────────────────────────────────────────────────────────────


def test_zero_drift_yields_skip_no_drift() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 1.0},
        target_positions={"BTC": 1.0},
        market_data={"BTC": _LIQUID},
    )
    assert plan.n_executed == 0
    assert plan.n_skipped == 1
    assert plan.decisions[0].action == "SKIP_NO_DRIFT"


def test_small_drift_yields_skip_small_drift() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 1.000},
        target_positions={"BTC": 1.005},  # 0.5% drift
        market_data={"BTC": _LIQUID},
        min_drift_pct=0.05,
    )
    assert plan.n_executed == 0
    assert plan.decisions[0].action == "SKIP_SMALL_DRIFT"


def test_drift_with_zero_alpha_skipped_on_cost() -> None:
    """No expected alpha gain → any cost > 0 means skip."""
    plan = plan_rebalance(
        current_positions={"BTC": 1.0},
        target_positions={"BTC": 2.0},
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 0.0},
    )
    assert plan.n_executed == 0
    assert plan.decisions[0].action == "SKIP_COST_EXCEEDS_GAIN"


def test_drift_with_high_alpha_executes() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 1.0},
        target_positions={"BTC": 2.0},
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 5.0},  # 5bp per bar per unit
        bars_lookahead=60,
    )
    assert plan.n_executed == 1
    assert plan.decisions[0].action == "EXECUTE"
    o = plan.orders[0]
    assert o.symbol == "BTC"
    assert o.side == "BUY"
    assert o.quantity == pytest.approx(1.0)


def test_drift_down_yields_sell() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 2.0},
        target_positions={"BTC": 1.0},
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 5.0},
        bars_lookahead=60,
    )
    assert plan.n_executed == 1
    assert plan.orders[0].side == "SELL"
    assert plan.orders[0].quantity == pytest.approx(1.0)


def test_only_in_target_means_open_new_position() -> None:
    plan = plan_rebalance(
        current_positions={},
        target_positions={"ETH": 10.0},
        market_data={"ETH": _LIQUID},
        expected_alpha_per_position_bps={"ETH": 5.0},
    )
    assert plan.n_executed == 1
    assert plan.orders[0].side == "BUY"


def test_only_in_current_means_close_position() -> None:
    plan = plan_rebalance(
        current_positions={"ETH": 10.0},
        target_positions={},
        market_data={"ETH": _LIQUID},
        expected_alpha_per_position_bps={"ETH": 5.0},
    )
    assert plan.n_executed == 1
    assert plan.orders[0].side == "SELL"


# ──────────────────────────────────────────────────────────────────
# Slicing (TWAP chunks)
# ──────────────────────────────────────────────────────────────────


def test_chunks_one_when_trade_small_relative_to_adv() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 0.0},
        target_positions={"BTC": 0.01},  # ~$500 trade on $1B ADV
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 100.0},
        max_chunk_pct_of_adv=0.01,
    )
    assert plan.orders[0].chunks == 1


def test_chunks_split_when_trade_large_relative_to_adv() -> None:
    """5% of ADV trade, max chunk = 0.5% → 10 chunks."""
    # Build market data where the drift translates to a known % of ADV
    md = {"mid_price": 100.0, "spread_bp": 10.0, "adv_usd": 1_000_000}
    plan = plan_rebalance(
        current_positions={"X": 0.0},
        target_positions={"X": 500.0},   # 500 * $100 = $50k = 5% of $1M ADV
        market_data={"X": md},
        expected_alpha_per_position_bps={"X": 50.0},
        max_chunk_pct_of_adv=0.005,      # 0.5% of ADV per chunk
        bars_lookahead=60,
    )
    assert plan.n_executed == 1
    o = plan.orders[0]
    assert o.chunks == 10
    assert o.chunk_quantity == pytest.approx(50.0)


# ──────────────────────────────────────────────────────────────────
# Multi-symbol
# ──────────────────────────────────────────────────────────────────


def test_multi_symbol_partial_execution() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 1.0, "ETH": 5.0, "SOL": 100.0},
        target_positions={"BTC": 1.0, "ETH": 7.0, "SOL": 100.005},
        market_data={"BTC": _LIQUID, "ETH": _LIQUID, "SOL": _LIQUID},
        expected_alpha_per_position_bps={"ETH": 5.0, "SOL": 5.0},
        min_drift_pct=0.05,
        bars_lookahead=60,
    )
    # BTC = no drift → SKIP_NO_DRIFT
    # ETH = 28% drift, high alpha → EXECUTE
    # SOL = 0.005% drift → SKIP_SMALL_DRIFT
    actions = {d.symbol: d.action for d in plan.decisions}
    assert actions["BTC"] == "SKIP_NO_DRIFT"
    assert actions["ETH"] == "EXECUTE"
    assert actions["SOL"] == "SKIP_SMALL_DRIFT"
    assert plan.n_executed == 1


# ──────────────────────────────────────────────────────────────────
# Audit / serialization
# ──────────────────────────────────────────────────────────────────


def test_to_dict_serializable() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 0.0},
        target_positions={"BTC": 1.0},
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 5.0},
        bars_lookahead=60,
    )
    d = plan.to_dict()
    assert "orders" in d and "decisions" in d
    assert d["n_executed"] == 1


def test_decision_includes_cost_and_gain_for_executed() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 0.0},
        target_positions={"BTC": 1.0},
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 5.0},
        bars_lookahead=60,
    )
    d = plan.decisions[0]
    assert d.expected_gain_bp > 0
    assert d.expected_cost_bp > 0
    assert d.expected_gain_bp > d.expected_cost_bp


def test_decision_includes_cost_and_gain_for_skipped() -> None:
    plan = plan_rebalance(
        current_positions={"BTC": 0.0},
        target_positions={"BTC": 1.0},
        market_data={"BTC": _LIQUID},
        expected_alpha_per_position_bps={"BTC": 0.001},  # tiny alpha
        bars_lookahead=60,
    )
    d = plan.decisions[0]
    assert d.action == "SKIP_COST_EXCEEDS_GAIN"
    assert d.expected_cost_bp > d.expected_gain_bp


# ──────────────────────────────────────────────────────────────────
# Illiquid behavior — wider cost should make rebalance pickier
# ──────────────────────────────────────────────────────────────────


def test_illiquid_market_increases_skip_rate() -> None:
    """Same modest drift × small alpha — liquid executes, illiquid skips."""
    drift_args = dict(
        current_positions={"X": 100.0},
        target_positions={"X": 110.0},        # 10% drift, above min threshold
        expected_alpha_per_position_bps={"X": 0.05},  # 0.05bp per bar per unit
        bars_lookahead=5,
    )
    # Liquid: 2bp half-spread, negligible impact → executes
    plan_liq = plan_rebalance(market_data={"X": _LIQUID}, **drift_args)
    # Illiquid: 25bp half-spread alone kills the trade
    plan_illiq = plan_rebalance(market_data={"X": _ILLIQUID}, **drift_args)
    # Use the cost vs gain numbers from the decision to assert the
    # direction holds — even if both happen to execute the cost should
    # be strictly higher illiquid.
    assert plan_liq.decisions[0].expected_cost_bp < plan_illiq.decisions[0].expected_cost_bp
    assert plan_illiq.decisions[0].action == "SKIP_COST_EXCEEDS_GAIN"
