"""Live-vs-backtest performance drift monitor.

Backtests are a lower bound on what can go wrong in production — models
decay, regimes shift, counterparties change queue priority, fees tighten.
The monitor flags when live performance diverges from backtest expectations.

Signals tracked:
  - Rolling live Sharpe vs backtest Sharpe (z-score distance)
  - Live trade win-rate vs backtest win-rate
  - Realized volatility drift (Levene F-test proxy via variance ratio)
  - PSR-break: is the observed live SR statistically compatible with the
    backtest SR, or can we reject? (uses shared.statistics.deflated_sharpe)

Alerts are exposed via a lightweight API that ops / Prometheus can scrape.
State is kept in-process; persistence belongs to the caller.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from shared.statistics.deflated_sharpe import (
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)


@dataclass
class DriftAlert:
    level: str        # "ok" / "warn" / "breach"
    reason: str
    metrics: dict
    psr_reject: bool = False


@dataclass
class LiveDriftMonitor:
    strategy_id: str
    backtest_sharpe: float        # annualized
    backtest_volatility: float    # per-bar stdev of returns
    periods_per_year: float = 24 * 365
    window_bars: int = 500
    warn_z: float = 1.5
    breach_z: float = 2.5
    psr_break_threshold: float = 0.05  # reject if PSR < 5%
    _returns: deque = field(default_factory=lambda: deque(maxlen=500))
    _wins: deque = field(default_factory=lambda: deque(maxlen=500))

    def __post_init__(self):
        # Ensure deques respect window_bars
        self._returns = deque(maxlen=self.window_bars)
        self._wins = deque(maxlen=self.window_bars)

    # ------------------------------------------------------------------

    def observe(self, trade_return: float) -> None:
        self._returns.append(float(trade_return))
        self._wins.append(1.0 if trade_return > 0 else 0.0)

    # ------------------------------------------------------------------

    def evaluate(self) -> DriftAlert:
        n = len(self._returns)
        if n < max(30, self.window_bars // 10):
            return DriftAlert(
                level="ok",
                reason="insufficient_samples",
                metrics={"n": n},
            )
        arr = np.asarray(self._returns, dtype=float)
        live_sr = sharpe_ratio(arr, self.periods_per_year)
        live_vol = float(arr.std(ddof=1))
        # z-score of SR drift (Bailey approximation: SR stderr ≈ sqrt((1+SR²/2)/n))
        sr_bar = self.backtest_sharpe / math.sqrt(self.periods_per_year)
        se_sr_bar = math.sqrt((1 + sr_bar ** 2 / 2) / max(n - 1, 1))
        se_sr = se_sr_bar * math.sqrt(self.periods_per_year)
        z = (live_sr - self.backtest_sharpe) / max(se_sr, 1e-9)

        psr = probabilistic_sharpe_ratio(
            arr, sr_benchmark=self.backtest_sharpe, periods_per_year=self.periods_per_year,
        )
        psr_reject = (psr is not None) and not math.isnan(psr) and psr < self.psr_break_threshold
        # Variance drift via F-ratio
        var_ratio = (live_vol ** 2) / max(self.backtest_volatility ** 2, 1e-12)
        winrate = float(np.mean(self._wins)) if self._wins else 0.0

        metrics = {
            "n": n,
            "live_sharpe": round(live_sr, 3),
            "backtest_sharpe": round(self.backtest_sharpe, 3),
            "sharpe_z": round(float(z), 2),
            "psr_vs_backtest": round(psr, 4) if psr is not None else None,
            "live_vol": round(live_vol, 5),
            "var_ratio": round(var_ratio, 2),
            "winrate": round(winrate, 3),
        }

        if psr_reject or abs(z) >= self.breach_z:
            return DriftAlert(
                level="breach",
                reason="live_performance_rejects_backtest_distribution",
                metrics=metrics,
                psr_reject=psr_reject,
            )
        if abs(z) >= self.warn_z or var_ratio > 2.0 or var_ratio < 0.5:
            return DriftAlert(
                level="warn",
                reason="live_performance_drifting",
                metrics=metrics,
            )
        return DriftAlert(level="ok", reason="live_within_tolerance", metrics=metrics)
