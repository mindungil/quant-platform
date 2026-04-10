"""Adaptive timeframe selection.

Switches between 1h and 8h based on realized volatility regime:
  - High vol (engine thrives) → 1h (more signal, accept higher cost)
  - Low vol (engine struggles) → 8h (less signal, but lower cost saves it)

Uses a hysteresis mechanism to prevent thrashing: must hold the new
condition for `dwell_bars` before switching.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class TimeframeDecision:
    timeframe: str          # "1h" or "8h"
    vol_z: float            # current vol z-score
    reason: str             # human-readable reason
    bars_since_switch: int  # how many bars at current TF


class AdaptiveTimeframe:
    def __init__(
        self,
        vol_high_z: float = 0.5,
        vol_low_z: float = -0.5,
        dwell_bars: int = 12,
        default_tf: str = "1h",
        vol_window: int = 168,
        vol_baseline_window: int = 168 * 4,
        state_path: str | None = None,
    ):
        self.vol_high_z = vol_high_z
        self.vol_low_z = vol_low_z
        self.dwell_bars = dwell_bars
        self.default_tf = default_tf
        self.vol_window = vol_window
        self.vol_baseline_window = vol_baseline_window
        self._state_path = Path(state_path) if state_path else None
        self._current_tf = default_tf
        self._pending_tf: str | None = None
        self._pending_count = 0
        self._bars_at_current = 0
        self._load_state()

    def select(self, df_1h: pd.DataFrame) -> TimeframeDecision:
        """Select optimal timeframe based on current vol regime."""
        close = df_1h["close"].astype(float)
        log_ret = np.log(close / close.shift(1)).fillna(0.0)

        vol = log_ret.rolling(self.vol_window, min_periods=20).std(ddof=0)
        baseline = vol.rolling(self.vol_baseline_window, min_periods=50).mean()
        baseline_std = vol.rolling(self.vol_baseline_window, min_periods=50).std(ddof=0)
        vol_z = float(((vol - baseline) / baseline_std.replace(0, np.nan)).fillna(0.0).iloc[-1])

        # Determine desired TF
        if vol_z > self.vol_high_z:
            desired = "1h"
            reason = f"high vol (z={vol_z:+.2f} > {self.vol_high_z})"
        elif vol_z < self.vol_low_z:
            desired = "8h"
            reason = f"low vol (z={vol_z:+.2f} < {self.vol_low_z})"
        else:
            desired = self._current_tf
            reason = f"neutral vol (z={vol_z:+.2f}), hold {self._current_tf}"

        # Hysteresis: require dwell_bars consecutive signals
        if desired != self._current_tf:
            if desired == self._pending_tf:
                self._pending_count += 1
            else:
                self._pending_tf = desired
                self._pending_count = 1

            if self._pending_count >= self.dwell_bars:
                self._current_tf = desired
                self._pending_tf = None
                self._pending_count = 0
                self._bars_at_current = 0
                reason = f"SWITCHED to {desired}: {reason}"
        else:
            self._pending_tf = None
            self._pending_count = 0

        self._bars_at_current += 1
        self._save_state()

        return TimeframeDecision(
            timeframe=self._current_tf,
            vol_z=round(vol_z, 4),
            reason=reason,
            bars_since_switch=self._bars_at_current,
        )

    def _load_state(self):
        if self._state_path and self._state_path.exists():
            try:
                with open(self._state_path) as f:
                    s = json.load(f)
                self._current_tf = s.get("current_tf", self.default_tf)
                self._bars_at_current = s.get("bars_at_current", 0)
            except Exception:
                pass

    def _save_state(self):
        if self._state_path:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_path, "w") as f:
                json.dump({
                    "current_tf": self._current_tf,
                    "bars_at_current": self._bars_at_current,
                }, f)
