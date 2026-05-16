"""Online learning closed loop — production-ready orchestrator.

What this provides
------------------
A single `LearningLoop` object that the strategy-lab incubator daemon
(or any cron-style runner) calls once per cycle. Internally it manages
per-alpha `OnlineDSR` + `AlphaPauseDecider` instances and a shared
`FactorDecayMonitor`, persists their state through a pluggable backend,
and returns a structured list of decisions so the caller can:

  - flip a flag in the alpha registry / DB
  - publish a `learning.alpha.state_changed` event to NATS
  - emit Prometheus counters / Grafana annotations

This module is intentionally I/O-agnostic: pass `state_store=None` for
pure in-memory operation (great for tests + backtest replay), or pass a
`RedisStateStore` / `JsonFileStateStore` for production.

V2 dependencies (all already in shared/):
  - shared.statistics.online_dsr.OnlineDSR / AlphaPauseDecider
  - shared.factors.decay_monitor.FactorDecayMonitor
  - shared.learning.persist (round-trip helpers)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Protocol

from shared.factors.decay_monitor import FactorDecayMonitor
from shared.learning.persist import (
    decider_to_dict,
    dict_to_decider,
    dict_to_factor_monitor,
    dict_to_online_dsr,
    factor_monitor_to_dict,
    online_dsr_to_dict,
)
from shared.statistics.online_dsr import (
    AlphaPauseDecider,
    AlphaState,
    OnlineDSR,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Public dataclasses
# ──────────────────────────────────────────────────────────────────


@dataclass
class AlphaLoopResult:
    """Outcome of one bar's update for a single alpha."""

    alpha_name: str
    prev_state: AlphaState
    new_state: AlphaState
    state_changed: bool
    dsr: Optional[float]
    decision_reason: str

    def as_event(self) -> dict[str, Any]:
        """Payload suitable for publishing on a NATS subject."""
        return {
            "alpha_name": self.alpha_name,
            "prev_state": self.prev_state,
            "new_state": self.new_state,
            "state_changed": self.state_changed,
            "dsr": self.dsr,
            "decision_reason": self.decision_reason,
        }


@dataclass
class FactorLoopResult:
    factor_name: str
    prev_active_weight: float
    new_active_weight: float
    weight_changed: bool
    ic_ir: Optional[float]
    is_decayed: bool


@dataclass
class LearningLoopConfig:
    """Per-alpha DSR + decider defaults. Same values applied to every
    alpha the loop has seen (use per-alpha override via the loop API
    later if differentiation is needed)."""

    # OnlineDSR
    dsr_window_bars: int = 24 * 90      # 90 days at 1h cadence
    dsr_n_trials: int = 1
    dsr_sr_std_across_trials: float = 1.0
    dsr_periods_per_year: float = 24 * 365
    dsr_min_samples: int = 30

    # AlphaPauseDecider
    pause_threshold: float = 0.5
    recover_threshold: float = 0.7
    consecutive_required: int = 3

    # FactorDecayMonitor
    factor_ic_window: int = 30
    factor_ir_window: int = 90
    factor_ir_threshold: float = 0.2
    factor_min_observations: int = 0  # 0 → auto in monitor

    # Initial state when a new alpha first appears
    initial_state: AlphaState = "LIVE"


# ──────────────────────────────────────────────────────────────────
# State store protocol — pluggable persistence
# ──────────────────────────────────────────────────────────────────


class StateStore(Protocol):
    """Minimal key/value protocol so the loop doesn't depend on Redis."""

    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str) -> None: ...
    def keys(self, pattern: str) -> list[str]: ...


class InMemoryStateStore:
    """Trivial dict-backed store for tests + single-process daemons.

    `keys()` accepts full glob syntax via fnmatch — `learning:alpha:*:state`,
    `learning:factors:*`, etc. all work, matching the semantics of
    Redis's KEYS / SCAN MATCH.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def keys(self, pattern: str) -> list[str]:
        import fnmatch
        return [k for k in self._store if fnmatch.fnmatchcase(k, pattern)]


# ──────────────────────────────────────────────────────────────────
# LearningLoop
# ──────────────────────────────────────────────────────────────────


_KEY_ALPHA_DSR = "learning:alpha:{name}:dsr"
_KEY_ALPHA_STATE = "learning:alpha:{name}:state"
_KEY_ALPHA_DECIDER = "learning:alpha:{name}:decider"
_KEY_FACTOR_MONITOR = "learning:factors:monitor"


@dataclass
class LearningLoop:
    """Run one update cycle and return state transitions.

    Lifecycle:
        loop = LearningLoop(config, state_store=RedisStateStore(...))
        loop.warm_start()  # restore from state_store

        # On every bar:
        result = loop.update_alpha_pnl("momentum_ensemble", bar_pnl)
        if result.state_changed:
            publish_event(result.as_event())

        # On every factor evaluation:
        fr = loop.update_factor_ic("momentum_z", score, fwd_ret)

        # Periodically:
        loop.checkpoint()  # flush in-memory state back to store
    """

    config: LearningLoopConfig = field(default_factory=LearningLoopConfig)
    state_store: Optional[StateStore] = None
    _alpha_dsr: dict[str, OnlineDSR] = field(default_factory=dict, init=False)
    _alpha_state: dict[str, AlphaState] = field(default_factory=dict, init=False)
    _alpha_decider: dict[str, AlphaPauseDecider] = field(default_factory=dict, init=False)
    _factor_monitor: FactorDecayMonitor = field(init=False)

    def __post_init__(self) -> None:
        kwargs = {
            "ic_window": self.config.factor_ic_window,
            "ir_window": self.config.factor_ir_window,
            "ir_threshold": self.config.factor_ir_threshold,
        }
        if self.config.factor_min_observations > 0:
            kwargs["min_observations"] = self.config.factor_min_observations
        self._factor_monitor = FactorDecayMonitor(**kwargs)

    # ─── boot ────────────────────────────────────────────────────

    def warm_start(self) -> None:
        """Reload all per-alpha and factor state from the store. Safe to
        call repeatedly; later updates win."""
        if self.state_store is None:
            return
        for key in self.state_store.keys("learning:alpha:*:state"):
            name = key.split(":")[2]
            raw = self.state_store.get(key)
            if raw:
                self._alpha_state[name] = raw  # type: ignore[assignment]
            dsr_raw = self.state_store.get(_KEY_ALPHA_DSR.format(name=name))
            if dsr_raw:
                try:
                    self._alpha_dsr[name] = dict_to_online_dsr(json.loads(dsr_raw))
                except Exception as exc:
                    logger.warning("warm_start_dsr_failed", extra={"name": name, "err": str(exc)[:120]})
            dec_raw = self.state_store.get(_KEY_ALPHA_DECIDER.format(name=name))
            if dec_raw:
                try:
                    self._alpha_decider[name] = dict_to_decider(json.loads(dec_raw))
                except Exception as exc:
                    logger.warning("warm_start_decider_failed", extra={"name": name, "err": str(exc)[:120]})
        fm_raw = self.state_store.get(_KEY_FACTOR_MONITOR)
        if fm_raw:
            try:
                self._factor_monitor = dict_to_factor_monitor(json.loads(fm_raw))
            except Exception as exc:
                logger.warning("warm_start_factor_monitor_failed", extra={"err": str(exc)[:120]})

    # ─── core API ────────────────────────────────────────────────

    def update_alpha_pnl(
        self,
        alpha_name: str,
        pnl_per_bar: float,
    ) -> AlphaLoopResult:
        """Push one bar PnL for `alpha_name`. Returns the decision."""
        odsr = self._alpha_dsr.get(alpha_name)
        if odsr is None:
            odsr = self._build_dsr()
            self._alpha_dsr[alpha_name] = odsr
        decider = self._alpha_decider.get(alpha_name)
        if decider is None:
            decider = self._build_decider()
            self._alpha_decider[alpha_name] = decider
        prev_state = self._alpha_state.get(alpha_name, self.config.initial_state)

        snapshot = odsr.update(pnl_per_bar)
        dsr_val = snapshot["dsr"] if snapshot else None
        new_state = decider.step(dsr_val, prev_state)
        self._alpha_state[alpha_name] = new_state

        return AlphaLoopResult(
            alpha_name=alpha_name,
            prev_state=prev_state,
            new_state=new_state,
            state_changed=(new_state != prev_state),
            dsr=dsr_val,
            decision_reason=decider.last_decision_reason,
        )

    def update_factor_ic(
        self,
        factor_name: str,
        score: float,
        forward_return: float,
    ) -> FactorLoopResult:
        prev_active = self._factor_monitor.active_weight(factor_name)
        self._factor_monitor.record(factor_name, score, forward_return)
        new_active = self._factor_monitor.active_weight(factor_name)
        return FactorLoopResult(
            factor_name=factor_name,
            prev_active_weight=prev_active,
            new_active_weight=new_active,
            weight_changed=(prev_active != new_active),
            ic_ir=self._factor_monitor.current_ic_ir(factor_name),
            is_decayed=self._factor_monitor.is_decayed(factor_name),
        )

    # ─── batch / inspection ──────────────────────────────────────

    def snapshot_alphas(self) -> dict[str, dict[str, Any]]:
        """All tracked alphas with their current DSR/state — for dashboards."""
        out: dict[str, dict[str, Any]] = {}
        for name, odsr in self._alpha_dsr.items():
            snap = odsr.snapshot()
            decider = self._alpha_decider.get(name)
            out[name] = {
                "state": self._alpha_state.get(name, self.config.initial_state),
                "dsr": snap["dsr"] if snap else None,
                "n_samples": odsr.n_samples(),
                "last_decision_reason": decider.last_decision_reason if decider else "",
            }
        return out

    def snapshot_factors(self) -> dict[str, dict[str, Any]]:
        return self._factor_monitor.all_status()

    def get_alphas_by_state(self, state: AlphaState) -> list[str]:
        return [
            name for name, s in self._alpha_state.items()
            if s == state
        ]

    def get_decayed_factors(self) -> list[str]:
        return [
            name for name in self._factor_monitor._buffers
            if self._factor_monitor.is_decayed(name)
        ]

    # ─── persistence ─────────────────────────────────────────────

    def checkpoint(self) -> int:
        """Flush every in-memory record back to the state store.

        Returns the number of keys written (for ops sanity check).
        """
        if self.state_store is None:
            return 0
        written = 0
        for name, odsr in self._alpha_dsr.items():
            self.state_store.set(
                _KEY_ALPHA_DSR.format(name=name),
                json.dumps(online_dsr_to_dict(odsr)),
            )
            written += 1
        for name, state in self._alpha_state.items():
            self.state_store.set(_KEY_ALPHA_STATE.format(name=name), state)
            written += 1
        for name, decider in self._alpha_decider.items():
            self.state_store.set(
                _KEY_ALPHA_DECIDER.format(name=name),
                json.dumps(decider_to_dict(decider)),
            )
            written += 1
        self.state_store.set(
            _KEY_FACTOR_MONITOR,
            json.dumps(factor_monitor_to_dict(self._factor_monitor)),
        )
        written += 1
        return written

    # ─── internal ────────────────────────────────────────────────

    def _build_dsr(self) -> OnlineDSR:
        return OnlineDSR(
            window_bars=self.config.dsr_window_bars,
            n_trials=self.config.dsr_n_trials,
            sr_std_across_trials=self.config.dsr_sr_std_across_trials,
            periods_per_year=self.config.dsr_periods_per_year,
            min_samples=self.config.dsr_min_samples,
        )

    def _build_decider(self) -> AlphaPauseDecider:
        return AlphaPauseDecider(
            pause_threshold=self.config.pause_threshold,
            recover_threshold=self.config.recover_threshold,
            consecutive_required=self.config.consecutive_required,
        )

    # ─── convenience: bulk update ────────────────────────────────

    def update_alpha_pnl_bulk(
        self,
        pnl_per_alpha: Iterable[tuple[str, float]],
    ) -> list[AlphaLoopResult]:
        return [self.update_alpha_pnl(n, p) for n, p in pnl_per_alpha]

    def update_factor_ic_bulk(
        self,
        scores_and_returns: Iterable[tuple[str, float, float]],
    ) -> list[FactorLoopResult]:
        return [self.update_factor_ic(n, s, r) for n, s, r in scores_and_returns]
