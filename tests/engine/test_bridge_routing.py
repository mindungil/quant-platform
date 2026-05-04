"""Tests for signal_to_order_bridge mode routing + isolation guards.

Covers:
  - _log_dir_for_mode routes to separate directories per mode
  - run_virtual produces expected payload shape without touching the network
  - run_virtual respects the VirtualFuturesConnector tripwire
  - run_virtual never writes to data/paper/ or data/logs/execution/
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_bridge():
    spec = importlib.util.spec_from_file_location(
        "signal_to_order_bridge",
        REPO_ROOT / "scripts" / "live" / "signal_to_order_bridge.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ──────────────────────────────────────────────────────────────────
# Log directory routing
# ──────────────────────────────────────────────────────────────────

def test_log_dir_for_mode_routes_each_mode_to_distinct_dir():
    b = _load_bridge()
    dry = b._log_dir_for_mode("dry-run")
    virt = b._log_dir_for_mode("virtual")
    tnet = b._log_dir_for_mode("testnet")
    live = b._log_dir_for_mode("live")

    # All 4 are distinct OR testnet/live share one (real-money bucket)
    assert dry != virt
    assert virt != tnet
    assert virt != live
    assert dry != tnet
    # Testnet and live share execution/, virtual has its own
    assert "virtual_execution" in str(virt)
    assert "dry_run_execution" in str(dry)
    assert str(tnet) == str(live)  # real-money bucket


def test_write_execution_log_lands_in_mode_dir(tmp_path, monkeypatch):
    b = _load_bridge()
    monkeypatch.setattr(b, "EXEC_LOG_DIR", tmp_path / "execution")
    monkeypatch.setattr(b, "VIRTUAL_LOG_DIR", tmp_path / "virtual_execution")
    monkeypatch.setattr(b, "DRY_RUN_LOG_DIR", tmp_path / "dry_run_execution")
    p_dry = b.write_execution_log({"x": 1}, mode="dry-run")
    p_virt = b.write_execution_log({"x": 2}, mode="virtual")
    p_live = b.write_execution_log({"x": 3}, mode="live")
    assert "dry_run_execution" in str(p_dry)
    assert "virtual_execution" in str(p_virt)
    assert "execution" in str(p_live) and "virtual" not in str(p_live)


# ──────────────────────────────────────────────────────────────────
# run_virtual — end-to-end with fake prices
# ──────────────────────────────────────────────────────────────────

def test_run_virtual_executes_orders_without_api(tmp_path, monkeypatch):
    """Full pipeline: bridge → VirtualFuturesConnector → fills, no network."""
    from shared.execution.virtual_futures import VirtualFuturesConnector
    b = _load_bridge()

    # Intercept the connector to inject a deterministic price fetcher
    state_file = tmp_path / "virtual" / "state.json"
    hist_file = tmp_path / "virtual" / "history.jsonl"
    state_file.parent.mkdir(parents=True)

    fixed = {"BTCUSDT": 80_000.0, "ETHUSDT": 2_500.0}

    class StubConnector(VirtualFuturesConnector):
        def __init__(self, **kw):
            # Force injected fetcher regardless of bridge call args
            kw["price_fetcher"] = lambda symbols: {s: fixed[s] for s in symbols if s in fixed}
            super().__init__(**kw)

    import shared.execution.virtual_futures as vf
    monkeypatch.setattr(vf, "VirtualFuturesConnector", StubConnector)
    # The bridge imports from shared.execution.virtual_futures inside run_virtual,
    # so monkeypatching the module-level name will be honored on next import.

    signals = [
        {"symbol": "BTCUSDT", "target_position": 0.10, "price": 79_000.0, "parked": False},
        {"symbol": "ETHUSDT", "target_position": 0.20, "price": 2_450.0, "parked": False},
    ]

    payload = b.run_virtual(
        signals, initial_equity=10_000,
        max_pos_per_symbol=0.30, max_gross=1.0, max_dd=0.20,
        state_file=state_file, history_file=hist_file,
    )

    assert payload["mode"] == "virtual"
    assert payload["filled"] >= 1
    assert payload["equity_before"] == 10_000
    assert "positions_after" in payload
    # State file should exist under /virtual/
    assert state_file.exists()
    # History has fill records
    lines = hist_file.read_text().strip().split("\n")
    assert any(json.loads(ln).get("type") == "fill" for ln in lines)


def test_run_virtual_rejects_non_virtual_state_path(tmp_path):
    """Bridge → connector tripwire must reject a paper-path override."""
    b = _load_bridge()
    bad_state = tmp_path / "paper" / "state.json"
    bad_state.parent.mkdir(parents=True)
    import pytest
    with pytest.raises(ValueError, match="/virtual/"):
        b.run_virtual(
            signals=[], initial_equity=1000,
            max_pos_per_symbol=0.2, max_gross=1.0, max_dd=0.15,
            state_file=bad_state, history_file=tmp_path / "virtual" / "h.jsonl",
        )


# ──────────────────────────────────────────────────────────────────
# Build targets — same contract as before, sanity
# ──────────────────────────────────────────────────────────────────

def test_build_targets_still_works_via_bridge_module():
    b = _load_bridge()
    signals = [{"symbol": "BTCUSDT", "target_position": 0.1, "price": 80_000}]
    targets, log = b.build_targets(signals, equity=10_000, prices={"BTCUSDT": 80_000})
    assert targets["BTCUSDT"] == 0.0125  # 10000 * 0.1 / 80000
    assert log["BTCUSDT"]["parked"] is False
