"""Live readiness scorecard tests.

Cover the dimensions that have deterministic logic (no real Binance call):
- composite verdict thresholds
- alpha-health ratio enforcement
- realism-flag detection
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "live_readiness", REPO_ROOT / "scripts" / "live" / "live_readiness.py"
)
lr = importlib.util.module_from_spec(spec)
sys.modules["live_readiness"] = lr
spec.loader.exec_module(lr)


def _ds(name: str, score: int, weight: float = 1.0) -> "lr.DimensionScore":
    return lr.DimensionScore(name=name, score=score, weight=weight, detail="", reasons=[])


def test_composite_verdict_go():
    dims = [_ds("a", 90), _ds("b", 88), _ds("c", 85), _ds("d", 92), _ds("e", 90), _ds("f", 86)]
    composite, verdict = lr.composite_verdict(dims)
    assert composite >= 85
    assert verdict == "GO"


def test_composite_verdict_soft_no():
    dims = [_ds("a", 70), _ds("b", 65), _ds("c", 60), _ds("d", 75), _ds("e", 70), _ds("f", 60)]
    composite, verdict = lr.composite_verdict(dims)
    assert 60 <= composite < 85
    assert verdict == "SOFT-NO"


def test_composite_verdict_hard_no():
    dims = [_ds("a", 30), _ds("b", 50), _ds("c", 40), _ds("d", 50), _ds("e", 50), _ds("f", 30)]
    composite, verdict = lr.composite_verdict(dims)
    assert composite < 60
    assert verdict == "HARD-NO"


def test_alpha_health_penalises_failing_symbols():
    """When live SR is < 50% of OOS expected, score should drop."""
    fake_state = {
        "backtest_expectations": {
            "btc": {"sr": 0.4},
            "eth": {"sr": 1.0},
            "bnb": {"sr": 0.5},
        },
        "alpha_health": {
            "BTC": {"live_sr": -1.0},  # ratio = -2.5  → fail
            "ETH": {"live_sr": 1.5},   # ratio = +1.5  → pass
            "BNB": {"live_sr": 0.05},  # ratio = +0.10 → fail
        },
    }
    with patch.object(lr, "_read_json", return_value=fake_state):
        result = lr.score_alpha_health()
    # 100 - 25*2 (BTC + BNB fails) = 50
    assert result.score == 50
    assert any("BTC" in r for r in result.reasons)
    assert any("BNB" in r for r in result.reasons)


def test_alpha_health_all_passing():
    fake_state = {
        "backtest_expectations": {
            "btc": {"sr": 0.4},
            "eth": {"sr": 1.0},
            "bnb": {"sr": 0.5},
        },
        "alpha_health": {
            "BTC": {"live_sr": 0.5},   # ratio = 1.25
            "ETH": {"live_sr": 1.2},   # ratio = 1.2
            "BNB": {"live_sr": 0.6},   # ratio = 1.2
        },
    }
    with patch.object(lr, "_read_json", return_value=fake_state):
        result = lr.score_alpha_health()
    assert result.score == 100
    assert result.reasons == []


def test_realism_funding_explicitly_off_penalised(monkeypatch):
    """User explicitly disabled funding → harsh penalty."""
    monkeypatch.setenv("PAPER_SIM_FUNDING", "false")
    result = lr.score_realism()
    assert result.score <= 50
    assert any("funding NOT charged" in r for r in result.reasons)


def test_api_permissions_no_creds_skipped():
    result = lr.score_api_permissions(None, None)
    assert result.score == 50
    assert "skipped" in result.detail
