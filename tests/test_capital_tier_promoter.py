"""Tests for scripts.live.capital_tier_promoter (G21-G25).

Focused on the bits that have actual logic: drawdown normalization
(was the bug we hit during G21 verification — peak-relative DD blew
past 100% on small-dollar PnL) and the dry-run guarantee.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.live import capital_tier_promoter as ctp  # type: ignore


# ──────────────────────────────────────────────────────────────────
# drawdown: NAV-normalized worst excursion
# ──────────────────────────────────────────────────────────────────


def test_drawdown_zero_when_only_gains():
    per_strategy = {"a": [0.5, 0.3, 0.2]}  # monotonically rising
    assert ctp._aggregate_drawdown(per_strategy, nav=1000) == 0.0


def test_drawdown_excursion_over_nav():
    # peak at +5, trough at -3 → excursion = 8 USD
    per_strategy = {"a": [5.0, -2.0, -6.0]}
    dd = ctp._aggregate_drawdown(per_strategy, nav=1000.0)
    assert abs(dd - 8.0 / 1000.0) < 1e-9


def test_drawdown_zero_when_nav_missing():
    # Earlier version returned inflated DD when peak was tiny.
    # Now we explicitly refuse to compute without NAV.
    per_strategy = {"a": [0.4, -1.5]}
    assert ctp._aggregate_drawdown(per_strategy, nav=0.0) == 0.0


def test_drawdown_interleaves_strategies():
    # Two strategies, one strictly winning, one strictly losing —
    # the aggregate should net out at each step.
    per_strategy = {
        "win": [1.0, 1.0, 1.0],
        "loss": [-0.5, -0.5, -0.5],
    }
    # Aggregate cumulative: 0.5, 1.0, 1.5 — monotone, no drawdown.
    assert ctp._aggregate_drawdown(per_strategy, nav=100.0) == 0.0


# ──────────────────────────────────────────────────────────────────
# aggregate Sharpe
# ──────────────────────────────────────────────────────────────────


def test_aggregate_sharpe_below_min_trades_returns_zero():
    rows = [{"fills": 30, "naive_sharpe": 2.0}]  # n_trades total < 50
    assert ctp._aggregate_sharpe(rows) == 0.0


def test_aggregate_sharpe_weighted_by_fills():
    rows = [
        {"fills": 100, "naive_sharpe": 1.0},
        {"fills": 100, "naive_sharpe": 3.0},
    ]
    # weighted = (100*1 + 100*3) / 200 = 2.0
    assert ctp._aggregate_sharpe(rows) == 2.0


def test_aggregate_sharpe_handles_none():
    rows = [
        {"fills": 100, "naive_sharpe": None},
        {"fills": 100, "naive_sharpe": 1.0},
    ]
    # None should be treated as 0.0
    assert ctp._aggregate_sharpe(rows) == 0.5


# ──────────────────────────────────────────────────────────────────
# tier resolution from Prometheus
# ──────────────────────────────────────────────────────────────────


def test_current_tier_from_prometheus_handles_each_index(monkeypatch):
    expected = ["PAPER", "MICRO", "SMALL", "MID", "FULL"]
    for i, name in enumerate(expected):
        monkeypatch.setattr(ctp, "_prom_scalar", lambda q, v=i: float(v))
        assert ctp._current_tier_from_prometheus() == name


def test_current_tier_from_prometheus_returns_none_on_missing(monkeypatch):
    monkeypatch.setattr(ctp, "_prom_scalar", lambda q: None)
    assert ctp._current_tier_from_prometheus() is None


def test_current_tier_from_prometheus_returns_none_on_out_of_range(monkeypatch):
    monkeypatch.setattr(ctp, "_prom_scalar", lambda q: 99.0)
    assert ctp._current_tier_from_prometheus() is None


# ──────────────────────────────────────────────────────────────────
# evaluate(): dry-run guarantee
# ──────────────────────────────────────────────────────────────────


def test_evaluate_never_applies_in_dry_run(monkeypatch):
    """The whole reason this script exists is to NOT auto-apply.
    Force a promotion-eligible TierStats and verify the in-process
    tier doesn't move when apply_transition=False.
    """
    from shared.risk import capital_tier

    capital_tier._active_tier = "MICRO"

    promoting_stats = capital_tier.TierStats(
        n_trades=200,
        realized_sharpe=2.0,
        realized_max_dd=0.01,
        hard_kill_events=0,
    )
    monkeypatch.setattr(
        ctp, "compute_tier_stats",
        lambda hours: (promoting_stats, {"nav_usd": 10_000.0}),
    )
    monkeypatch.setattr(ctp, "_current_tier_from_prometheus", lambda: "MICRO")
    monkeypatch.setattr(ctp, "_get_redis", lambda: None)

    verdict = ctp.evaluate(hours=24, apply_transition=False)

    assert verdict["suggested_tier"] == "SMALL"
    assert verdict["would_apply"] is False
    assert capital_tier.current_tier() == "MICRO"  # NOT advanced


def test_evaluate_applies_only_when_explicitly_opted_in(monkeypatch):
    from shared.risk import capital_tier

    capital_tier._active_tier = "MICRO"

    promoting_stats = capital_tier.TierStats(
        n_trades=200,
        realized_sharpe=2.0,
        realized_max_dd=0.01,
        hard_kill_events=0,
    )
    monkeypatch.setattr(
        ctp, "compute_tier_stats",
        lambda hours: (promoting_stats, {"nav_usd": 10_000.0}),
    )
    monkeypatch.setattr(ctp, "_current_tier_from_prometheus", lambda: "MICRO")
    monkeypatch.setattr(ctp, "_get_redis", lambda: None)

    verdict = ctp.evaluate(hours=24, apply_transition=True)

    assert verdict["suggested_tier"] == "SMALL"
    assert verdict["would_apply"] is True
    assert capital_tier.current_tier() == "SMALL"

    # Reset for other tests
    capital_tier._active_tier = "PAPER"
