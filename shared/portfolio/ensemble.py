"""Multi-strategy ensemble allocator.

Takes a panel of alpha signals (each in [-1, 1]) and produces a final
target-position series. Three combine modes:

  - "equal":         arithmetic mean of all signals
  - "inverse_vol":   weight each alpha by 1/vol(its returns), normalized
  - "hrp":           Hierarchical Risk Parity over alpha return correlations
                     (uses shared.portfolio.hrp under the hood)

The output is then vol-targeted: scale the combined signal so realized vol
matches `target_vol_annual`. A hard kill switch zeroes the signal if rolling
drawdown exceeds `kill_drawdown`.

This is the layer that turns "I have 6 backtested alphas" into "here's the
notional weight to send to the order service this minute". It is bar-time
deterministic and side-effect-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from shared.portfolio.hrp import hrp_weights


@dataclass
class EnsembleConfig:
    combine_mode: str = "inverse_vol"      # equal | inverse_vol | hrp
    vol_lookback: int = 168                # bars for vol estimation
    target_vol_annual: float = 0.20
    periods_per_year: int = 252 * 24       # hourly default
    max_gross: float = 1.0                 # cap on |combined position|
    max_per_alpha: float = 0.6             # cap on any single alpha's weight
    kill_drawdown: float = 0.20            # zero the signal beyond this rolling DD
    kill_window: int = 720                 # rolling DD window
    min_history: int = 100                 # min bars before any allocation fires
    rebalance_every: int = 1               # bars between weight refreshes


@dataclass
class EnsembleResult:
    target_position: pd.Series             # final, vol-targeted, kill-switched
    raw_combined: pd.Series                # before vol-targeting and kill switch
    alpha_weights: pd.DataFrame            # per-bar weight on each alpha
    diagnostics: dict = field(default_factory=dict)


class EnsembleAllocator:
    def __init__(self, config: EnsembleConfig | None = None) -> None:
        self.config = config or EnsembleConfig()

    def combine(
        self,
        alpha_positions: dict[str, pd.Series],
        underlying_returns: pd.Series,
    ) -> EnsembleResult:
        """Combine alpha positions into a single target-position series.

        Args:
            alpha_positions: {alpha_name: position_series in [-1,1]}
            underlying_returns: bar-level pct change of the asset (used for
                                vol estimation and ensemble pnl tracking)
        """
        if not alpha_positions:
            empty = pd.Series(dtype=float)
            return EnsembleResult(
                target_position=empty,
                raw_combined=empty,
                alpha_weights=pd.DataFrame(),
                diagnostics={"reason": "no_alphas"},
            )

        # Align everything to a common index
        positions_df = pd.DataFrame(alpha_positions).fillna(0.0)
        positions_df = positions_df.reindex(underlying_returns.index).fillna(0.0)
        ret = underlying_returns.reindex(positions_df.index).fillna(0.0)

        # Per-alpha bar returns: position * underlying return
        alpha_returns = positions_df.multiply(ret, axis=0)

        weights = self._compute_weights(alpha_returns)

        # Apply per-alpha cap. The classical "cap then renormalize" loop
        # over-shoots when uncapped weights remain — we use a closed-form
        # waterfilling: clip the over-cap weights, then redistribute the
        # spare mass uniformly across the under-cap weights, repeat until
        # stable. Bounded by n_alphas iterations.
        cap = self.config.max_per_alpha
        n_alphas = weights.shape[1]
        weights_arr = weights.values.copy()
        for _ in range(n_alphas):
            over = weights_arr > cap + 1e-12
            if not over.any():
                break
            # For each row, set over to cap and rescale the rest
            for r in range(weights_arr.shape[0]):
                row = weights_arr[r]
                if row.sum() <= 0:
                    continue
                over_mask = row > cap + 1e-12
                if not over_mask.any():
                    continue
                excess = (row[over_mask] - cap).sum()
                row[over_mask] = cap
                under_mask = ~over_mask & (row > 0)
                if under_mask.any():
                    under_total = row[under_mask].sum()
                    if under_total > 0:
                        row[under_mask] += excess * (row[under_mask] / under_total)
                weights_arr[r] = row
        # Final clamp + renormalize
        weights_arr = np.clip(weights_arr, 0.0, cap)
        row_sums = weights_arr.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        weights_arr = weights_arr / row_sums
        # Numerical safety: clamp once more in case division reintroduced overshoot
        weights_arr = np.clip(weights_arr, 0.0, cap)
        weights = pd.DataFrame(weights_arr, index=weights.index, columns=weights.columns)

        # Combined position = Σ w_i * pos_i
        combined = (positions_df * weights).sum(axis=1)
        combined = combined.clip(-self.config.max_gross, self.config.max_gross)

        # Vol target: scale so realized vol == target
        scale = self._vol_target_scale(combined * ret)
        target = (combined * scale).clip(-self.config.max_gross, self.config.max_gross)

        # Kill switch
        target = self._apply_kill_switch(target, target * ret)

        return EnsembleResult(
            target_position=target,
            raw_combined=combined,
            alpha_weights=weights,
            diagnostics={
                "n_alphas": int(positions_df.shape[1]),
                "combine_mode": self.config.combine_mode,
                "avg_target_abs": float(target.abs().mean()),
                "max_target_abs": float(target.abs().max()),
                "active_pct": float((target.abs() > 0.05).mean()),
            },
        )

    # ---- weighting strategies ----

    def _compute_weights(self, alpha_returns: pd.DataFrame) -> pd.DataFrame:
        n = alpha_returns.shape[1]
        idx = alpha_returns.index
        cols = alpha_returns.columns
        weights = pd.DataFrame(0.0, index=idx, columns=cols)

        mode = self.config.combine_mode
        win = self.config.vol_lookback
        rebalance = max(1, self.config.rebalance_every)
        min_hist = self.config.min_history
        last_w = np.full(n, 1.0 / n)

        for i in range(len(alpha_returns)):
            if i < min_hist:
                continue
            if i % rebalance != 0:
                weights.iloc[i] = last_w
                continue

            window = alpha_returns.iloc[max(0, i - win) : i]
            if len(window) < 20:
                last_w = np.full(n, 1.0 / n)
            elif mode == "equal":
                last_w = np.full(n, 1.0 / n)
            elif mode == "inverse_vol":
                vols = window.std(ddof=0).values
                vols = np.where(vols > 1e-9, vols, np.nan)
                if np.all(np.isnan(vols)):
                    last_w = np.full(n, 1.0 / n)
                else:
                    inv = np.where(np.isnan(vols), 0.0, 1.0 / vols)
                    s = inv.sum()
                    last_w = inv / s if s > 0 else np.full(n, 1.0 / n)
            elif mode == "hrp":
                arr = window.values
                # Need at least 2 alphas with non-zero variance
                non_flat = (arr.std(axis=0) > 1e-9).sum()
                if non_flat < 2:
                    last_w = np.full(n, 1.0 / n)
                else:
                    res = hrp_weights(arr, list(cols))
                    last_w = np.array([res["weights"].get(c, 0.0) for c in cols])
            else:
                last_w = np.full(n, 1.0 / n)

            weights.iloc[i] = last_w

        return weights

    # ---- vol targeting ----

    def _vol_target_scale(self, returns: pd.Series) -> pd.Series:
        win = self.config.vol_lookback
        target = self.config.target_vol_annual
        annualizer = np.sqrt(self.config.periods_per_year)

        rolling_vol = returns.rolling(win, min_periods=max(20, win // 4)).std(ddof=0) * annualizer
        scale = (target / rolling_vol.replace(0, np.nan)).clip(0.0, 3.0)
        return scale.fillna(1.0)

    # ---- kill switch ----

    def _apply_kill_switch(self, position: pd.Series, returns: pd.Series) -> pd.Series:
        win = self.config.kill_window
        kill = self.config.kill_drawdown

        equity = (1.0 + returns).cumprod()
        rolling_peak = equity.rolling(win, min_periods=1).max()
        rolling_dd = (equity - rolling_peak) / rolling_peak

        # Once we breach `kill`, zero the signal until DD recovers above -kill/2
        out = position.copy()
        in_kill = False
        for i in range(len(position)):
            dd = rolling_dd.iloc[i]
            if not in_kill and dd <= -kill:
                in_kill = True
            elif in_kill and dd >= -kill / 2:
                in_kill = False
            if in_kill:
                out.iloc[i] = 0.0
        return out
