"""Tests for smart_upbit dynamic L2-aware slice planning (no network)."""
from __future__ import annotations

import pytest

from shared.execution.smart_upbit import SmartExecConfig, _plan_slices
from shared.execution.upbit_l2 import OrderbookSnapshot


def _thin_book(levels: int = 10, per_level_btc: float = 0.02) -> OrderbookSnapshot:
    bids = [(100_000_000 - i * 50_000, per_level_btc) for i in range(levels)]
    asks = [(100_000_000 + i * 50_000, per_level_btc) for i in range(levels)]
    return OrderbookSnapshot("KRW-BTC", 0, bids, asks)


def test_planner_single_slice_below_impact_budget():
    snap = _thin_book(per_level_btc=10.0)  # very deep book
    cfg = SmartExecConfig(max_impact_bps=8.0)
    # 10M on a 10 BTC/level book is trivial → 1 slice
    assert _plan_slices(snap, "BUY", 10_000_000, cfg) == 1


def test_planner_splits_when_order_exceeds_impact_budget():
    snap = _thin_book(per_level_btc=0.02)  # 2M per level, 20M total
    cfg = SmartExecConfig(max_impact_bps=8.0)
    assert _plan_slices(snap, "BUY", 20_000_000, cfg) >= 2
    assert _plan_slices(snap, "BUY", 50_000_000, cfg) >= 3


def test_planner_respects_hard_ceiling():
    snap = _thin_book(per_level_btc=0.001)  # extremely thin
    cfg = SmartExecConfig(max_impact_bps=5.0)
    # Even wild impact shouldn't produce absurdly many slices
    n = _plan_slices(snap, "BUY", 500_000_000, cfg)
    assert 1 <= n <= 20


def test_planner_legacy_fallback_without_snapshot():
    cfg = SmartExecConfig(twap_threshold_krw=500_000, twap_slices=4)
    assert _plan_slices(None, "BUY", 300_000, cfg) == 1
    assert _plan_slices(None, "BUY", 3_000_000, cfg) == 4


def test_planner_disabled_l2_uses_legacy_path():
    snap = _thin_book(per_level_btc=0.0001)  # book where L2 *would* split
    cfg = SmartExecConfig(use_l2_sizing=False, twap_threshold_krw=500_000, twap_slices=4)
    # With L2 disabled we follow the fixed threshold logic only
    assert _plan_slices(snap, "BUY", 300_000, cfg) == 1
    assert _plan_slices(snap, "BUY", 3_000_000, cfg) == 4
