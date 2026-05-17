"""Tests for shared.risk.monitor_hub — V4-5 quick win."""
from __future__ import annotations

import pytest

from shared.risk.monitor_hub import (
    RiskEvent,
    clear_kill,
    clear_notifiers,
    clear_throttle,
    current_size_multiplier,
    emit,
    is_killed,
    register_notifier,
    slack_webhook_notifier,
    snapshot,
)


@pytest.fixture(autouse=True)
def _reset():
    """Clear all state between tests."""
    clear_notifiers()
    clear_kill("global")
    clear_kill("crypto-agent")
    clear_throttle("global")
    yield
    clear_notifiers()
    clear_kill("global")
    clear_kill("crypto-agent")
    clear_throttle("global")


# ─── RiskEvent validation ───────────────────────────────────────────


def test_event_class_validated() -> None:
    with pytest.raises(ValueError):
        RiskEvent(event_class="WAT", reason="x")  # type: ignore[arg-type]


def test_soft_event_requires_multiplier() -> None:
    with pytest.raises(ValueError):
        RiskEvent(event_class="SOFT", reason="throttle")


def test_multiplier_must_be_in_range() -> None:
    with pytest.raises(ValueError):
        RiskEvent(event_class="SOFT", reason="x", multiplier=1.5)
    with pytest.raises(ValueError):
        RiskEvent(event_class="SOFT", reason="x", multiplier=-0.1)
    RiskEvent(event_class="SOFT", reason="x", multiplier=0.5)


# ─── HARD kill lifecycle ────────────────────────────────────────────


def test_hard_kill_sets_active() -> None:
    assert not is_killed()
    emit(RiskEvent(event_class="HARD", reason="drawdown_30pct", scope="global",
                   detail="DD hit -30%"))
    assert is_killed()
    assert current_size_multiplier() == 0.0


def test_hard_kill_scope_isolated() -> None:
    emit(RiskEvent(event_class="HARD", reason="x", scope="crypto-agent"))
    assert is_killed("crypto-agent")
    assert not is_killed("global")
    assert current_size_multiplier("crypto-agent") == 0.0
    assert current_size_multiplier("global") == 1.0


def test_clear_kill_resets() -> None:
    emit(RiskEvent(event_class="HARD", reason="x"))
    assert is_killed()
    clear_kill()
    assert not is_killed()
    assert current_size_multiplier() == 1.0


# ─── SOFT throttle lifecycle ────────────────────────────────────────


def test_soft_throttle_applies_multiplier() -> None:
    emit(RiskEvent(event_class="SOFT", reason="vol_spike", multiplier=0.3))
    assert current_size_multiplier() == 0.3


def test_kill_overrides_throttle() -> None:
    emit(RiskEvent(event_class="SOFT", reason="vol", multiplier=0.5))
    emit(RiskEvent(event_class="HARD", reason="kill"))
    assert current_size_multiplier() == 0.0


def test_clear_throttle_resets_to_one() -> None:
    emit(RiskEvent(event_class="SOFT", reason="vol", multiplier=0.4))
    clear_throttle()
    assert current_size_multiplier() == 1.0


# ─── OBSERVATION events ──────────────────────────────────────────────


def test_obs_event_does_not_change_state() -> None:
    emit(RiskEvent(event_class="OBS", reason="dead_alpha_flagged"))
    assert not is_killed()
    assert current_size_multiplier() == 1.0


# ─── Notifier fan-out ────────────────────────────────────────────────


def test_notifier_invoked_for_every_event() -> None:
    received = []
    register_notifier(received.append)

    emit(RiskEvent(event_class="HARD", reason="x"))
    emit(RiskEvent(event_class="SOFT", reason="y", multiplier=0.5))
    emit(RiskEvent(event_class="OBS", reason="z"))
    assert len(received) == 3
    assert received[0].event_class == "HARD"


def test_notifier_failures_do_not_break_emit() -> None:
    def bad(_): raise RuntimeError("oops")
    received = []
    register_notifier(bad)
    register_notifier(received.append)
    emit(RiskEvent(event_class="OBS", reason="x"))
    assert len(received) == 1  # the good notifier still fired


# ─── Snapshot ────────────────────────────────────────────────────────


def test_snapshot_shape() -> None:
    emit(RiskEvent(event_class="HARD", reason="dd", scope="global"))
    emit(RiskEvent(event_class="SOFT", reason="vol", scope="crypto-agent", multiplier=0.3))
    snap = snapshot()
    assert "active_kills" in snap
    assert "active_throttles" in snap
    assert "global" in snap["active_kills"]
    assert "crypto-agent" in snap["active_throttles"]
    assert snap["active_throttles"]["crypto-agent"] == 0.3


# ─── Slack webhook helper ────────────────────────────────────────────


def test_slack_webhook_notifier_factory_returns_callable() -> None:
    fn = slack_webhook_notifier("https://hooks.slack.com/test")
    # Don't actually POST — just verify the factory shape
    assert callable(fn)
    # Calling it with no webhook server should swallow the network error
    fn(RiskEvent(event_class="OBS", reason="dry_run"))
