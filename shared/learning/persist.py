"""JSON serialization for the three V2 monitor objects.

Used by `LearningLoop` to round-trip per-alpha and per-factor state
through Redis (or any other key/value store). Deliberately schema-free
on top — the caller decides where each blob lives.

All three converters are pure functions: they never touch Redis. The
LearningLoop adapter is responsible for the I/O.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from shared.factors.decay_monitor import FactorDecayMonitor, _FactorBuffer
from shared.statistics.online_dsr import AlphaPauseDecider, OnlineDSR


# ──────────────────────────────────────────────────────────────────
# OnlineDSR
# ──────────────────────────────────────────────────────────────────


def online_dsr_to_dict(odsr: OnlineDSR) -> dict[str, Any]:
    return {
        "window_bars": odsr.window_bars,
        "n_trials": odsr.n_trials,
        "sr_std_across_trials": odsr.sr_std_across_trials,
        "periods_per_year": odsr.periods_per_year,
        "min_samples": odsr.min_samples,
        "returns": list(odsr._returns),
    }


def dict_to_online_dsr(data: dict[str, Any]) -> OnlineDSR:
    odsr = OnlineDSR(
        window_bars=int(data["window_bars"]),
        n_trials=int(data.get("n_trials", 1)),
        sr_std_across_trials=float(data.get("sr_std_across_trials", 1.0)),
        periods_per_year=float(data.get("periods_per_year", 24 * 365)),
        min_samples=int(data.get("min_samples", 30)),
    )
    for r in data.get("returns", []):
        odsr._returns.append(float(r))
    return odsr


# ──────────────────────────────────────────────────────────────────
# AlphaPauseDecider — pure config + streak counters, no time series
# ──────────────────────────────────────────────────────────────────


def decider_to_dict(decider: AlphaPauseDecider) -> dict[str, Any]:
    return {
        "pause_threshold": decider.pause_threshold,
        "recover_threshold": decider.recover_threshold,
        "consecutive_required": decider.consecutive_required,
        "_bad_streak": decider._bad_streak,
        "_good_streak": decider._good_streak,
        "last_decision_reason": decider.last_decision_reason,
    }


def dict_to_decider(data: dict[str, Any]) -> AlphaPauseDecider:
    d = AlphaPauseDecider(
        pause_threshold=float(data.get("pause_threshold", 0.5)),
        recover_threshold=float(data.get("recover_threshold", 0.7)),
        consecutive_required=int(data.get("consecutive_required", 3)),
    )
    d._bad_streak = int(data.get("_bad_streak", 0))
    d._good_streak = int(data.get("_good_streak", 0))
    d.last_decision_reason = str(data.get("last_decision_reason", ""))
    return d


# ──────────────────────────────────────────────────────────────────
# FactorDecayMonitor._FactorBuffer (per factor blob)
# ──────────────────────────────────────────────────────────────────


def factor_buffer_to_dict(buf: _FactorBuffer) -> dict[str, Any]:
    return {
        "scores": list(buf.scores),
        "forward_returns": list(buf.forward_returns),
        "ic_history": list(buf.ic_history),
        "total_records": buf.total_records,
    }


def dict_to_factor_buffer(
    data: dict[str, Any],
    *,
    ic_window: int,
    ir_window: int,
) -> _FactorBuffer:
    """Reconstruct a buffer with the right deque maxlens (set by the
    monitor's configured windows)."""
    buf = _FactorBuffer(
        scores=deque(maxlen=ic_window),
        forward_returns=deque(maxlen=ic_window),
        ic_history=deque(maxlen=ir_window),
        total_records=int(data.get("total_records", 0)),
    )
    for s in data.get("scores", []):
        buf.scores.append(float(s))
    for r in data.get("forward_returns", []):
        buf.forward_returns.append(float(r))
    for ic in data.get("ic_history", []):
        buf.ic_history.append(float(ic))
    return buf


def factor_monitor_to_dict(monitor: FactorDecayMonitor) -> dict[str, Any]:
    """Full FactorDecayMonitor snapshot — config + every tracked factor."""
    return {
        "ic_window": monitor.ic_window,
        "ir_window": monitor.ir_window,
        "ir_threshold": monitor.ir_threshold,
        "min_observations": monitor.min_observations,
        "use_spearman": monitor.use_spearman,
        "buffers": {
            name: factor_buffer_to_dict(buf)
            for name, buf in monitor._buffers.items()
        },
    }


def dict_to_factor_monitor(data: dict[str, Any]) -> FactorDecayMonitor:
    monitor = FactorDecayMonitor(
        ic_window=int(data.get("ic_window", 30)),
        ir_window=int(data.get("ir_window", 90)),
        ir_threshold=float(data.get("ir_threshold", 0.2)),
        min_observations=int(data.get("min_observations", 0)),
        use_spearman=bool(data.get("use_spearman", True)),
    )
    for name, buf_data in data.get("buffers", {}).items():
        monitor._buffers[name] = dict_to_factor_buffer(
            buf_data,
            ic_window=monitor.ic_window,
            ir_window=monitor.ir_window,
        )
    return monitor
