"""Tests for app.core.tca helpers — pure functions used by outcome_consumer.

This test imports from a service-internal path so it runs against the
crypto-agent's app/ layout. Marked IP because it tests behavior on top of
bandit (private).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make crypto-agent's app/ importable for this test
_AGENT_DIR = Path("/home/ubuntu/quant/services/crypto-agent")
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import pytest

from app.core.tca import (
    TCAResult,
    compute_realized_slippage_bp,
    compute_tca_reward,
)


# ──────────────────────────────────────────────────────────────────
# compute_realized_slippage_bp
# ──────────────────────────────────────────────────────────────────


def test_buy_adverse_slippage_is_positive() -> None:
    """BUY at 102 when ref was 100 → paid 200 bp over → adverse, positive."""
    assert compute_realized_slippage_bp(102.0, 100.0, "BUY") == pytest.approx(200.0)


def test_buy_favorable_fill_is_negative() -> None:
    """BUY at 99 when ref was 100 → favorable, negative."""
    assert compute_realized_slippage_bp(99.0, 100.0, "BUY") == pytest.approx(-100.0)


def test_sell_adverse_slippage_is_positive() -> None:
    """SELL at 98 when ref was 100 → received less → adverse, positive (oriented)."""
    assert compute_realized_slippage_bp(98.0, 100.0, "SELL") == pytest.approx(200.0)


def test_sell_favorable_fill_is_negative() -> None:
    """SELL at 101 when ref was 100 → favorable."""
    assert compute_realized_slippage_bp(101.0, 100.0, "SELL") == pytest.approx(-100.0)


def test_zero_reference_returns_zero() -> None:
    """No reference price → no slippage signal."""
    assert compute_realized_slippage_bp(100.0, 0.0, "BUY") == 0.0


def test_negative_prices_return_zero() -> None:
    """Defensive against bad upstream data."""
    assert compute_realized_slippage_bp(-1.0, 100.0, "BUY") == 0.0
    assert compute_realized_slippage_bp(100.0, -1.0, "BUY") == 0.0


def test_side_is_case_insensitive() -> None:
    assert compute_realized_slippage_bp(102.0, 100.0, "buy") == pytest.approx(200.0)
    assert compute_realized_slippage_bp(98.0, 100.0, "sell") == pytest.approx(200.0)


# ──────────────────────────────────────────────────────────────────
# compute_tca_reward
# ──────────────────────────────────────────────────────────────────


def test_tca_reward_pnl_minus_cost() -> None:
    """Profitable trade with 50bp adverse slippage → reward = pnl - 50bp."""
    r = compute_tca_reward(
        pnl=0.020,           # +2%
        fill_price=100.5,    # 50bp adverse for BUY
        reference_price=100.0,
        side="BUY",
    )
    assert r.reward_source == "pnl_minus_tca"
    assert r.realized_slippage_bp == pytest.approx(50.0)
    assert r.tca_cost_bp == pytest.approx(50.0)
    assert r.tca_adjusted_reward == pytest.approx(0.020 - 0.0050)


def test_tca_reward_no_pnl_pure_cost_signal() -> None:
    """No realized PnL → bandit still sees negative reward for slippage."""
    r = compute_tca_reward(
        pnl=0.0,
        fill_price=100.3,
        reference_price=100.0,
        side="BUY",
    )
    assert r.reward_source == "tca_only"
    assert r.tca_adjusted_reward == pytest.approx(-0.0030)


def test_tca_reward_zero_cost_weight_returns_raw_pnl() -> None:
    """tca_cost_weight=0 disables the TCA correction."""
    r = compute_tca_reward(
        pnl=0.015, fill_price=110.0, reference_price=100.0, side="BUY",
        tca_cost_weight=0.0,
    )
    assert r.tca_cost_bp == 0.0
    assert r.tca_adjusted_reward == pytest.approx(0.015)


def test_tca_reward_favorable_slippage_does_not_credit_pnl() -> None:
    """Even a favorable fill incurs zero penalty (abs taken) but doesn't add
    bonus reward — it doesn't tell us anything about the formula's quality."""
    r = compute_tca_reward(
        pnl=0.010,
        fill_price=99.5,   # 50bp BETTER than ref for BUY
        reference_price=100.0,
        side="BUY",
    )
    # cost is |slip| * weight = 50 * 1.0 = 50bp = 0.0050
    assert r.realized_slippage_bp == pytest.approx(-50.0)
    assert r.tca_cost_bp == pytest.approx(50.0)
    # Note: this is a design choice. We could "credit" favorable slippage,
    # but per the module docstring we treat it as noise from execution.
    assert r.tca_adjusted_reward == pytest.approx(0.010 - 0.0050)


def test_tca_reward_zero_pnl_zero_slippage_is_none() -> None:
    """No signal anywhere → reward 0 + 'none' source."""
    r = compute_tca_reward(pnl=0.0, fill_price=100.0, reference_price=100.0,
                           side="BUY")
    assert r.reward_source == "none"
    assert r.tca_adjusted_reward == 0.0


def test_tca_reward_high_weight_amplifies_penalty() -> None:
    """tca_cost_weight > 1 → bandit treats slippage as worse than 1:1 with PnL."""
    r = compute_tca_reward(
        pnl=0.010, fill_price=100.2, reference_price=100.0, side="BUY",
        tca_cost_weight=3.0,
    )
    # cost = 20bp * 3 = 60bp = 0.006
    assert r.tca_cost_bp == pytest.approx(60.0)
    assert r.tca_adjusted_reward == pytest.approx(0.010 - 0.006)


def test_tca_reward_negative_weight_raises() -> None:
    with pytest.raises(ValueError):
        compute_tca_reward(pnl=0.01, fill_price=100.1, reference_price=100.0,
                           side="BUY", tca_cost_weight=-0.5)


def test_tca_reward_sell_path_flips_orientation() -> None:
    """SELL at 99.8 vs ref 100 → 20bp adverse (less revenue) → penalty applied."""
    r = compute_tca_reward(
        pnl=0.005, fill_price=99.8, reference_price=100.0, side="SELL",
    )
    assert r.realized_slippage_bp == pytest.approx(20.0)
    assert r.tca_adjusted_reward == pytest.approx(0.005 - 0.0020)
