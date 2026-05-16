"""Factor IC decay tracker — flags factors whose information ratio has
collapsed below a threshold and tells the live weighter to zero them.

Why this exists
---------------
Most factor libraries grow over time. New factors get registered, validated
on backtests, and rolled live. The opposite never happens automatically:
dead factors silently keep contributing noise to the IC-weighted score,
diluting the signal of factors that still work.

This module keeps a rolling per-factor (score, forward_return) buffer and
computes two metrics:

  IC      = Spearman rank correlation between factor scores and the
            corresponding forward returns over the last `ic_window` bars.
            Recomputed on every record().
  IC_IR   = mean(rolling IC) / std(rolling IC) over the last `ir_window`
            IC estimates. The classic information-ratio formulation.

When |IC_IR| < ir_threshold for a factor with enough observations, the
factor is flagged as 'decayed' and `active_weight()` returns 0 — the
live IC weighter can multiply this in to suppress the factor without
ripping it out of the registry. Once IC_IR recovers above the threshold,
the factor reactivates.

Pure scipy/numpy. No I/O. Safe to import in tests.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

import numpy as np
from scipy import stats as sp_stats


@dataclass
class _FactorBuffer:
    """Per-factor rolling buffer + IC history."""

    scores: Deque[float] = field(default_factory=deque)
    forward_returns: Deque[float] = field(default_factory=deque)
    ic_history: Deque[float] = field(default_factory=deque)
    total_records: int = 0  # lifetime count of record() calls


@dataclass
class FactorDecayMonitor:
    """Rolling IC + IC_IR tracker with auto-deprecate flag.

    Parameters
    ----------
    ic_window : int
        How many (score, return) pairs make one IC estimate. Default 30.
    ir_window : int
        How many rolling IC estimates feed the IR calculation. Default 90.
    ir_threshold : float
        |IC_IR| below this → flag as decayed. Default 0.2 — at IR < 0.2
        the factor is roughly indistinguishable from noise on a 1-year
        scale.
    min_observations : int
        Don't flag anything until this many (score, return) pairs have
        been recorded. Default = ic_window + ir_window so the first IR
        estimate is on a full history.
    use_spearman : bool
        Use Spearman rank correlation (default). Robust to outliers and
        the typical heavy-tailed-return / clipped-score combination.
        Set False to use Pearson.
    """

    ic_window: int = 30
    ir_window: int = 90
    ir_threshold: float = 0.2
    min_observations: int = 0  # 0 → auto-set in __post_init__
    use_spearman: bool = True
    _buffers: dict[str, _FactorBuffer] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.ic_window < 5:
            raise ValueError(f"ic_window must be ≥ 5, got {self.ic_window}")
        if self.ir_window < 5:
            raise ValueError(f"ir_window must be ≥ 5, got {self.ir_window}")
        if not (0.0 < self.ir_threshold <= 5.0):
            raise ValueError(f"ir_threshold must be in (0, 5], got {self.ir_threshold}")
        if self.min_observations <= 0:
            # Default = enough data for one full IR estimate
            self.min_observations = self.ic_window + self.ir_window

    def _get_buffer(self, factor_name: str) -> _FactorBuffer:
        buf = self._buffers.get(factor_name)
        if buf is None:
            buf = _FactorBuffer(
                scores=deque(maxlen=self.ic_window),
                forward_returns=deque(maxlen=self.ic_window),
                ic_history=deque(maxlen=self.ir_window),
            )
            self._buffers[factor_name] = buf
        return buf

    # ─── recording ───────────────────────────────────────────────

    def record(
        self,
        factor_name: str,
        score: float,
        forward_return: float,
    ) -> None:
        """Append one (score, forward_return) pair.

        Uses *non-overlapping* IC windows: every `ic_window` records, one
        fresh IC is appended to history and the buffer resets. Overlapping
        (sliding) windows produce serially-correlated IC estimates that
        deflate the IC_IR variance and yield spurious-large IRs even on
        pure noise — block sampling avoids that bias at the cost of one
        IC per `ic_window` records instead of one per record.
        """
        buf = self._get_buffer(factor_name)
        buf.scores.append(float(score))
        buf.forward_returns.append(float(forward_return))
        buf.total_records += 1
        if len(buf.scores) >= self.ic_window:
            ic = self._compute_ic(buf.scores, buf.forward_returns)
            if ic is not None:
                buf.ic_history.append(ic)
            buf.scores.clear()
            buf.forward_returns.clear()

    # ─── metric accessors ────────────────────────────────────────

    def current_ic(self, factor_name: str) -> Optional[float]:
        """Most-recently computed IC, or None if window not yet full."""
        buf = self._buffers.get(factor_name)
        if buf is None or not buf.ic_history:
            return None
        return float(buf.ic_history[-1])

    def current_ic_ir(self, factor_name: str) -> Optional[float]:
        """mean(IC) / std(IC) over the IR window. None if too few IC samples."""
        buf = self._buffers.get(factor_name)
        if buf is None or len(buf.ic_history) < max(self.ir_window // 3, 5):
            return None
        arr = np.fromiter(buf.ic_history, dtype=float, count=len(buf.ic_history))
        std = float(arr.std(ddof=1))
        if std <= 1e-12:
            return None
        return float(arr.mean() / std)

    def n_observations(self, factor_name: str) -> int:
        """Total (score, return) pairs ever recorded for this factor."""
        buf = self._buffers.get(factor_name)
        if buf is None:
            return 0
        return buf.total_records

    # ─── status flags ────────────────────────────────────────────

    def is_decayed(self, factor_name: str) -> bool:
        """True iff |IC_IR| < threshold AND we have enough observations."""
        if self.n_observations(factor_name) < self.min_observations:
            return False
        ir = self.current_ic_ir(factor_name)
        if ir is None:
            return False
        return abs(ir) < self.ir_threshold

    def active_weight(self, factor_name: str) -> float:
        """1.0 when healthy or warming up, 0.0 when decayed."""
        return 0.0 if self.is_decayed(factor_name) else 1.0

    def status(self, factor_name: str) -> dict:
        """Full diagnostic snapshot — for logging/dashboards."""
        return {
            "factor": factor_name,
            "n_obs": self.n_observations(factor_name),
            "current_ic": self.current_ic(factor_name),
            "ic_ir": self.current_ic_ir(factor_name),
            "is_decayed": self.is_decayed(factor_name),
            "active_weight": self.active_weight(factor_name),
        }

    def all_status(self) -> dict[str, dict]:
        return {name: self.status(name) for name in self._buffers}

    # ─── internal ────────────────────────────────────────────────

    def _compute_ic(
        self,
        scores: Deque[float],
        forward_returns: Deque[float],
    ) -> Optional[float]:
        s = np.fromiter(scores, dtype=float, count=len(scores))
        r = np.fromiter(forward_returns, dtype=float, count=len(forward_returns))
        # Need variance in both — constant series can't be correlated.
        if s.std() < 1e-12 or r.std() < 1e-12:
            return None
        if self.use_spearman:
            corr, _ = sp_stats.spearmanr(s, r)
        else:
            corr, _ = sp_stats.pearsonr(s, r)
        if np.isnan(corr):
            return None
        return float(corr)
