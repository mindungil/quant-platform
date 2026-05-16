"""Online (rolling-window) Deflated Sharpe Ratio + auto-pause state machine.

Wraps the batch DSR/PSR functions in shared.statistics.deflated_sharpe with
an incremental wrapper suitable for live monitoring of an alpha that is
producing one bar-return at a time.

Two consumers:

  OnlineDSR — maintains a fixed-size deque of returns and recomputes the
              DSR on every update. With a 90-day × 24-hour window (2160
              bars) the per-update cost is O(window), which is negligible
              compared to the rest of the decision loop. No incremental
              moment math required.

  AlphaPauseDecider — sticky state machine. Watches a stream of DSR scores
              and switches an alpha between LIVE and SHADOW. Requires the
              score to remain below pause_threshold for N consecutive
              evaluations before pausing (avoids noise-driven flapping)
              and above recover_threshold for N consecutive checks before
              re-promoting.

Both are pure Python — no Redis, no DB. Live incubator code is responsible
for persisting state and triggering side effects (writing the SHADOW flag
to the alpha registry, emitting a Prometheus counter, etc.).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Literal, Optional

from shared.statistics.deflated_sharpe import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)


AlphaState = Literal["LIVE", "SHADOW"]


# ──────────────────────────────────────────────────────────────────
# Online DSR
# ──────────────────────────────────────────────────────────────────


@dataclass
class OnlineDSR:
    """Maintain a rolling window of returns and emit DSR on each update.

    Parameters
    ----------
    window_bars : int
        Number of most-recent bars to keep. 90d × 24h = 2160 for a
        crypto 1h cadence; 90d × 78 = 7020 for US-equity 5min, etc.
    n_trials : int
        How many alphas were tried before this one was selected.
        Multi-testing correction inside DSR scales with this.
    sr_std_across_trials : float
        Standard deviation of annualized SR across the trials. The
        incubator should pass the observed value from its candidate
        population; passing 1.0 is a conservative default.
    periods_per_year : float
        Annualization factor. 24*365 for hourly crypto.
    min_samples : int
        Minimum bars required before emitting a DSR (returns None
        until reached). 30 is the floor from the PSR formula.
    """

    window_bars: int
    n_trials: int = 1
    sr_std_across_trials: float = 1.0
    periods_per_year: float = 24 * 365
    min_samples: int = 30
    _returns: Deque[float] = field(default_factory=deque, init=False)

    def __post_init__(self) -> None:
        if self.window_bars < self.min_samples:
            raise ValueError(
                f"window_bars ({self.window_bars}) must be ≥ min_samples ({self.min_samples})"
            )
        self._returns = deque(maxlen=self.window_bars)

    def update(self, ret: float) -> Optional[dict]:
        """Push the next bar return; return current DSR dict or None if warmup."""
        self._returns.append(float(ret))
        if len(self._returns) < self.min_samples:
            return None
        return self.snapshot()

    def snapshot(self) -> Optional[dict]:
        """Compute DSR on the current window without consuming a new return."""
        if len(self._returns) < self.min_samples:
            return None
        import numpy as np
        arr = np.fromiter(self._returns, dtype=float, count=len(self._returns))
        return deflated_sharpe_ratio(
            arr,
            n_trials=self.n_trials,
            sr_std_across_trials=self.sr_std_across_trials,
            periods_per_year=self.periods_per_year,
        )

    def n_samples(self) -> int:
        return len(self._returns)

    def reset(self) -> None:
        self._returns.clear()


# ──────────────────────────────────────────────────────────────────
# Auto-Pause state machine
# ──────────────────────────────────────────────────────────────────


@dataclass
class AlphaPauseDecider:
    """Sticky state machine for LIVE ↔ SHADOW transitions.

    A single bad day shouldn't pause an alpha — that lets noise drive
    the decision. We require `consecutive_required` evaluations on the
    wrong side of the threshold before flipping.

    Default thresholds (DSR space, [0, 1]):
      pause_threshold = 0.5  — coin-flip likelihood the SR is genuine
      recover_threshold = 0.7  — well above coin-flip, with margin to
                                 avoid bouncing right back to LIVE on
                                 the recovery edge.

    Use:
        decider = AlphaPauseDecider()
        state = "LIVE"
        for dsr in stream_of_dsr_snapshots():
            state = decider.step(dsr, state)
            if state == "SHADOW":
                ...pull the alpha from the live ensemble...
    """

    pause_threshold: float = 0.5
    recover_threshold: float = 0.7
    consecutive_required: int = 3
    _bad_streak: int = field(default=0, init=False)
    _good_streak: int = field(default=0, init=False)
    last_decision_reason: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if not (0.0 <= self.pause_threshold <= self.recover_threshold <= 1.0):
            raise ValueError(
                f"need 0 ≤ pause_threshold ({self.pause_threshold}) ≤ "
                f"recover_threshold ({self.recover_threshold}) ≤ 1"
            )
        if self.consecutive_required < 1:
            raise ValueError("consecutive_required must be ≥ 1")

    def step(self, dsr: Optional[float], current_state: AlphaState) -> AlphaState:
        """Advance the state machine with one DSR observation.

        dsr=None means insufficient data — treat as 'no signal' (state
        unchanged, both streaks zeroed so we don't accidentally pause
        on a long warmup period).
        """
        if dsr is None:
            self._bad_streak = 0
            self._good_streak = 0
            self.last_decision_reason = "warmup_no_data"
            return current_state

        if current_state == "LIVE":
            if dsr < self.pause_threshold:
                self._bad_streak += 1
                self._good_streak = 0
                if self._bad_streak >= self.consecutive_required:
                    self.last_decision_reason = (
                        f"paused_dsr_{dsr:.3f}<{self.pause_threshold:.2f}"
                        f"_for_{self._bad_streak}_checks"
                    )
                    self._bad_streak = 0  # reset on transition
                    return "SHADOW"
                self.last_decision_reason = (
                    f"warning_{self._bad_streak}/{self.consecutive_required}"
                )
                return "LIVE"
            else:
                self._bad_streak = 0
                self._good_streak = 0
                self.last_decision_reason = "healthy"
                return "LIVE"

        # current_state == "SHADOW"
        if dsr > self.recover_threshold:
            self._good_streak += 1
            self._bad_streak = 0
            if self._good_streak >= self.consecutive_required:
                self.last_decision_reason = (
                    f"promoted_dsr_{dsr:.3f}>{self.recover_threshold:.2f}"
                    f"_for_{self._good_streak}_checks"
                )
                self._good_streak = 0
                return "LIVE"
            self.last_decision_reason = (
                f"recovering_{self._good_streak}/{self.consecutive_required}"
            )
            return "SHADOW"
        else:
            self._good_streak = 0
            self._bad_streak = 0
            self.last_decision_reason = "still_shadow"
            return "SHADOW"

    def reset_streaks(self) -> None:
        self._bad_streak = 0
        self._good_streak = 0


# ──────────────────────────────────────────────────────────────────
# Convenience: one-shot rolling-DSR-from-history
# ──────────────────────────────────────────────────────────────────


def rolling_dsr_from_history(
    returns: list[float],
    window_bars: int,
    n_trials: int = 1,
    sr_std_across_trials: float = 1.0,
    periods_per_year: float = 24 * 365,
) -> list[Optional[dict]]:
    """Replay a return stream through OnlineDSR and collect all snapshots.

    Useful for backtest-style audit of when an alpha would have been
    paused historically. Returns one entry per input return.
    """
    odsr = OnlineDSR(
        window_bars=window_bars,
        n_trials=n_trials,
        sr_std_across_trials=sr_std_across_trials,
        periods_per_year=periods_per_year,
    )
    return [odsr.update(r) for r in returns]
