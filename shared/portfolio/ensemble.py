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
from shared.portfolio.nco import NCOConfig, nco_weights


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
    # Tail-hedge: vol z-score above this triggers an extra position scaledown
    tail_vol_z_threshold: float = 3.0
    tail_vol_window: int = 168
    tail_position_scale: float = 0.3       # multiply position by this when triggered
    # v3.1: self-Sharpe confidence gate. When the rolling Sharpe of the
    # combined signal turns negative, scale positions down. Acts as a
    # "trend-follow your own equity curve" guard against dead markets.
    self_sharpe_window: int = 720          # ~30d hourly
    self_sharpe_floor: float = 0.3         # minimum scale when self-sharpe is bad
    self_sharpe_full: float = 0.5          # sharpe at which scale = 1
    enable_self_sharpe_gate: bool = True
    # v3.3: per-alpha online performance gate. Each alpha gets a multiplicative
    # weight in [alpha_floor, 1] based on its *own* rolling Sharpe. Dead/negative
    # alphas auto-decay; recovering alphas auto-revive. This is the "self-improving"
    # layer — no human needs to drop a stale alpha.
    enable_alpha_gate: bool = True
    alpha_gate_window: int = 720           # rolling lookback for per-alpha sharpe
    alpha_gate_min_history: int = 240      # bars before gate activates (warmup)
    alpha_gate_floor: float = 0.0          # min multiplier (0 = full kill allowed)
    alpha_gate_full: float = 0.5           # sharpe at which multiplier = 1
    alpha_gate_kill_below: float = -0.5    # sharpe below this → multiplier = floor
    # v3.3: turnover hysteresis. After all gating/scaling, if the new target
    # differs from the previous applied target by less than `turnover_deadzone`
    # (in absolute notional units), keep the previous target — no trade.
    # Cuts thrashing 30-60% in practice with marginal Sharpe loss. Set to 0
    # to disable.
    turnover_deadzone: float = 0.10


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
        regime_proba: pd.DataFrame | None = None,
        regime_alpha_affinity: dict[str, dict[str, float]] | None = None,
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

        # v3.3: per-alpha online performance gate (auto-decay dead alphas)
        if self.config.enable_alpha_gate:
            alpha_gate = self._compute_alpha_gate(alpha_returns)
            weights = weights * alpha_gate
            # Renormalize after gating; if everything is killed, fall back uniform
            row_sums = weights.sum(axis=1)
            zero_rows = row_sums <= 1e-12
            if zero_rows.any():
                n = weights.shape[1]
                weights.loc[zero_rows] = 1.0 / n
                row_sums = weights.sum(axis=1)
            weights = weights.div(row_sums.replace(0, 1.0), axis=0)

        # Regime-aware reweighting: multiply each alpha's weight by its
        # affinity to the current regime, then renormalize. Affinity is
        # an external prior — typically: trend alphas like TREND_*, MR
        # alphas like RANGE, vol-breakout likes CRISIS-recovery, etc.
        if regime_proba is not None and regime_alpha_affinity is not None:
            regime_proba = regime_proba.reindex(positions_df.index).ffill().fillna(1.0 / regime_proba.shape[1])
            for alpha_name in positions_df.columns:
                affinity = regime_alpha_affinity.get(alpha_name, {})
                if not affinity:
                    continue
                # Weighted affinity at each bar = Σ_state proba(state) * affinity(state)
                aff_series = pd.Series(0.0, index=positions_df.index)
                for state, score in affinity.items():
                    if state in regime_proba.columns:
                        aff_series = aff_series + regime_proba[state] * float(score)
                weights[alpha_name] = weights[alpha_name] * aff_series
            # Renormalize after regime weighting
            row_sums = weights.sum(axis=1).replace(0, 1.0)
            weights = weights.div(row_sums, axis=0)

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

        # Tail hedge: check rolling vol z-score and scale down if extreme
        target = self._apply_tail_hedge(target, ret)

        # v3.1: self-Sharpe gate (adaptive confidence)
        if self.config.enable_self_sharpe_gate:
            target = self._apply_self_sharpe_gate(target, ret)

        # Kill switch
        target = self._apply_kill_switch(target, target * ret)

        # v3.3: turnover hysteresis (final step — applied to fully resolved target)
        if self.config.turnover_deadzone > 0:
            target = self._apply_hysteresis(target)

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
            elif mode == "nco":
                arr = window.values
                non_flat = (arr.std(axis=0) > 1e-9).sum()
                if non_flat < 2:
                    last_w = np.full(n, 1.0 / n)
                else:
                    cov = np.cov(arr, rowvar=False)
                    last_w = nco_weights(
                        cov, NCOConfig(max_clusters=min(4, n), n_obs_for_denoise=len(window))
                    )
                    if not np.isfinite(last_w).all() or last_w.sum() <= 0:
                        last_w = np.full(n, 1.0 / n)
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

    # ---- tail hedge ----

    def _apply_tail_hedge(self, position: pd.Series, returns: pd.Series) -> pd.Series:
        win = self.config.tail_vol_window
        thr = self.config.tail_vol_z_threshold
        scale = self.config.tail_position_scale
        rolling_vol = returns.rolling(win, min_periods=20).std(ddof=0)
        baseline = rolling_vol.rolling(win * 4, min_periods=50).mean()
        baseline_std = rolling_vol.rolling(win * 4, min_periods=50).std(ddof=0)
        vol_z = ((rolling_vol - baseline) / baseline_std.replace(0, np.nan)).fillna(0.0)
        out = position.copy()
        mask = vol_z > thr
        out[mask] = out[mask] * scale
        return out

    # ---- per-alpha online gate (v3.3) ----

    def _compute_alpha_gate(self, alpha_returns: pd.DataFrame) -> pd.DataFrame:
        """Per-alpha multiplicative gate from causal rolling Sharpe.

        For each alpha, compute the rolling annualized Sharpe of its OWN
        per-bar pnl over `alpha_gate_window` bars, SHIFTED BY 1 to enforce
        causality (gate at bar t uses info up to bar t-1). Map through
        a piecewise-linear ramp:
            sh ≤ kill_below → floor
            kill_below < sh < full → linear ramp floor..1
            sh ≥ full → 1.0
        Before warmup, gate = 1 (no-op).
        """
        win = int(self.config.alpha_gate_window)
        min_hist = int(self.config.alpha_gate_min_history)
        floor = float(self.config.alpha_gate_floor)
        full = float(self.config.alpha_gate_full)
        kill = float(self.config.alpha_gate_kill_below)
        annualizer = float(np.sqrt(self.config.periods_per_year))

        gate = pd.DataFrame(1.0, index=alpha_returns.index, columns=alpha_returns.columns)
        denom = max(full - kill, 1e-6)
        for col in alpha_returns.columns:
            r = alpha_returns[col]
            mean = r.rolling(win, min_periods=min_hist).mean()
            std = r.rolling(win, min_periods=min_hist).std(ddof=0).replace(0, np.nan)
            sharpe = (mean / std).fillna(0.0) * annualizer
            # Causal: at bar t, use sharpe computed through t-1
            sharpe = sharpe.shift(1).fillna(0.0)
            # Linear ramp
            ramp = floor + (sharpe - kill) * (1.0 - floor) / denom
            ramp = ramp.clip(floor, 1.0)
            # Before warmup, leave the gate at 1.0 (don't punish cold start)
            warm = mean.shift(1).notna()
            gate[col] = ramp.where(warm, 1.0)
        return gate

    # ---- self-sharpe gate ----

    def _apply_self_sharpe_gate(self, position: pd.Series, returns: pd.Series) -> pd.Series:
        """Scale position by rolling Sharpe of own PnL.

        Estimates rolling annualized Sharpe of position*ret over `self_sharpe_window`
        bars. Maps it through a piecewise linear ramp:
            sharpe ≤ -1 → scale = self_sharpe_floor
            -1 < sharpe < self_sharpe_full → linear
            sharpe ≥ self_sharpe_full → scale = 1
        """
        win = int(self.config.self_sharpe_window)
        floor = float(self.config.self_sharpe_floor)
        full = float(self.config.self_sharpe_full)
        annualizer = float(np.sqrt(self.config.periods_per_year))
        pnl = position * returns
        mean = pnl.rolling(win, min_periods=max(50, win // 4)).mean()
        std = pnl.rolling(win, min_periods=max(50, win // 4)).std(ddof=0).replace(0, np.nan)
        rolling_sharpe = (mean / std).fillna(0.0) * annualizer
        # Linear ramp from -1.0 to full → floor..1
        scale = floor + (rolling_sharpe + 1.0) * (1.0 - floor) / max(full + 1.0, 1e-6)
        scale = scale.clip(floor, 1.0)
        return position * scale

    # ---- turnover hysteresis (v3.3) ----

    def _apply_hysteresis(self, position: pd.Series) -> pd.Series:
        """Suppress micro-trades: keep previous applied target unless |Δpos| > deadzone.

        Walks the series in chronological order. Causal by construction.
        """
        dz = float(self.config.turnover_deadzone)
        arr = position.values.astype(float)
        out = np.empty_like(arr)
        prev = 0.0
        for i in range(len(arr)):
            new = arr[i]
            if abs(new - prev) >= dz:
                prev = new
            out[i] = prev
        return pd.Series(out, index=position.index)

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
