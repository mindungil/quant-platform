"""Tests for the shadow trading recorder.

Uses the in-memory fallback (SQL store unreachable in tests).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.shadow import ShadowFill, ShadowRecorder

UTC = timezone.utc


def make_recorder() -> ShadowRecorder:
    # Force the SQL fallback by passing an unreachable URL
    rec = ShadowRecorder(sql_url="postgresql+psycopg://invalid:invalid@127.0.0.1:1/unreachable")
    rec._sql = None  # belt-and-braces
    rec._fills.clear()
    return rec


def fill(strategy_id: str, pnl: float, t_offset: int = 0) -> ShadowFill:
    return ShadowFill(
        strategy_id=strategy_id,
        user_id="alice",
        asset="BTCUSDT",
        side="BUY",
        quantity=0.1,
        entry_price=30000.0,
        exit_price=30000.0 + pnl * 10,
        pnl=pnl,
        realized=True,
        timestamp=datetime.now(UTC) + timedelta(minutes=t_offset),
    )


def test_record_and_snapshot_basics():
    rec = make_recorder()
    for i in range(20):
        rec.record_fill(fill("strat-1", pnl=1.0 if i % 2 == 0 else -0.5))
    snap = rec.snapshot("strat-1")
    assert snap is not None
    assert snap.trade_count == 20
    assert snap.pnl == pytest.approx(20 * 0.5 * (1.0 - 0.5))  # 10*1 - 10*0.5 = 5
    assert 0 < snap.win_rate < 1


def test_snapshot_returns_none_without_realized():
    rec = make_recorder()
    snap = rec.snapshot("nothing-here")
    assert snap is None


def test_sharpe_positive_for_winning_strategy():
    rec = make_recorder()
    for _ in range(30):
        rec.record_fill(fill("winner", pnl=1.0))
    snap = rec.snapshot("winner")
    # All positive constant pnl → std=0 → sharpe=0 by guard
    assert snap is not None
    assert snap.sharpe == 0.0  # constant returns
    # Now add some variation
    rec2 = make_recorder()
    for i in range(30):
        rec2.record_fill(fill("varied", pnl=1.0 if i % 3 != 0 else -0.3))
    snap2 = rec2.snapshot("varied")
    assert snap2.sharpe > 0
    assert snap2.win_rate > 0.5


def test_max_drawdown_tracks_cumulative():
    rec = make_recorder()
    pnls = [1, 1, 1, -2, -1, 1, 1]
    for p in pnls:
        rec.record_fill(fill("dd-test", pnl=float(p)))
    snap = rec.snapshot("dd-test")
    assert snap is not None
    assert snap.max_drawdown >= 0


def test_payload_format_matches_registry_schema():
    rec = make_recorder()
    for i in range(15):
        rec.record_fill(fill("payload-test", pnl=1.0 if i % 2 == 0 else -0.5))
    snap = rec.snapshot("payload-test")
    payload = snap.to_payload()
    # Must match strategy-registry's ShadowMetricsUpdate fields
    for field in ("pnl", "trade_count", "sharpe", "max_drawdown", "win_rate"):
        assert field in payload
