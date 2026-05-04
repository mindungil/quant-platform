"""Tests for signal_to_order_bridge pure logic (build_targets)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Load bridge module by path — scripts/ is not a package, and we only
# test the pure build_targets logic (no exchange calls).
_spec = importlib.util.spec_from_file_location(
    "signal_to_order_bridge",
    REPO_ROOT / "scripts" / "live" / "signal_to_order_bridge.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_targets = _mod.build_targets


def _sig(symbol, target_position=0.0, price=100.0, parked=False, guard="ACTIVE", reason=None):
    s = {
        "symbol": symbol,
        "target_position": target_position,
        "price": price,
        "parked": parked,
        "live_guard": guard,
    }
    if reason:
        s["parked_reason"] = reason
    return s


def test_active_signal_produces_proportional_quantity():
    signals = [_sig("ETHUSDT", target_position=0.5, price=2000.0)]
    targets, log = build_targets(signals, equity=10_000, prices={"ETHUSDT": 2000.0})
    # notional = 10000 * 0.5 = 5000, qty = 5000/2000 = 2.5
    assert targets["ETHUSDT"] == 2.5
    assert log["ETHUSDT"]["parked"] is False
    assert log["ETHUSDT"]["notional"] == 5000.0


def test_parked_signal_forces_zero_quantity():
    signals = [_sig("SOLUSDT", target_position=0.5, price=80.0,
                    parked=True, guard="CONFIG_PARKED", reason="DSR suspect")]
    targets, log = build_targets(signals, equity=10_000, prices={"SOLUSDT": 80.0})
    assert targets["SOLUSDT"] == 0.0
    assert log["SOLUSDT"]["parked"] is True
    assert "DSR" in log["SOLUSDT"]["reason"]


def test_short_signal_produces_negative_quantity():
    signals = [_sig("BTCUSDT", target_position=-0.3, price=80_000.0)]
    targets, _ = build_targets(signals, equity=10_000, prices={"BTCUSDT": 80_000.0})
    # short 30% = -3000 notional / 80000 price = -0.0375
    assert targets["BTCUSDT"] == -0.0375


def test_error_signal_skipped():
    signals = [{"symbol": "BTCUSDT", "error": "fetch failed"}]
    targets, log = build_targets(signals, equity=10_000, prices={})
    assert "BTCUSDT" not in targets
    assert log["BTCUSDT"]["skip"] == "signal error"


def test_missing_price_skipped():
    signals = [_sig("XRPUSDT", target_position=0.1, price=0)]
    targets, log = build_targets(signals, equity=10_000, prices={"XRPUSDT": 0})
    assert "XRPUSDT" not in targets
    assert log["XRPUSDT"]["skip"] == "no price"


def test_exchange_price_overrides_signal_price():
    """Bridge should trust exchange mark price, not stale signal price."""
    signals = [_sig("ETHUSDT", target_position=0.2, price=2000.0)]
    # Exchange says ETH is 2500 — use that for quantity calc
    targets, log = build_targets(signals, equity=10_000, prices={"ETHUSDT": 2500.0})
    assert log["ETHUSDT"]["price"] == 2500.0
    assert targets["ETHUSDT"] == round(2000.0 / 2500.0, 6)  # 0.8


def test_mixed_parked_and_active_produces_correct_subset():
    signals = [
        _sig("BTCUSDT", target_position=0.2, price=80_000.0),
        _sig("SOLUSDT", target_position=0.5, price=80.0, parked=True, guard="CONFIG_PARKED", reason="no edge"),
        _sig("ETHUSDT", target_position=-0.1, price=2500.0, parked=True, guard="PARKED", reason="6M SR -0.8"),
    ]
    prices = {"BTCUSDT": 80_000, "SOLUSDT": 80, "ETHUSDT": 2500}
    targets, log = build_targets(signals, equity=10_000, prices=prices)
    assert targets["BTCUSDT"] > 0  # only active
    assert targets["SOLUSDT"] == 0
    assert targets["ETHUSDT"] == 0
    assert log["ETHUSDT"]["guard"] == "PARKED"
    assert log["SOLUSDT"]["guard"] == "CONFIG_PARKED"
