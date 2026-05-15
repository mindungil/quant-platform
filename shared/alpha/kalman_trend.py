"""Kalman local-level + slope trend filter alpha.

Maintains a hidden state (level, slope) updated by a Kalman filter on the
log-price observation. The position takes the sign of the filtered slope
and is sized by slope-to-noise ratio. Compared to EMA-based filters,
Kalman adapts the smoothing weight automatically when the underlying
volatility changes — less laggy in trends, less whippy in chop.

References:
- Harvey 1989, Forecasting, structural time series models and the Kalman filter
- Bouchaud et al. 2018, Two centuries of trend following

Local-linear-trend state-space model:
    level_{t+1}  = level_t + slope_t + ε_level
    slope_{t+1}  = slope_t           + ε_slope
    obs_t        = level_t           + ε_obs
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, atr, vol_target_scale


def _kalman_local_linear(
    y: np.ndarray,
    obs_var: float = 1e-4,
    level_var: float = 1e-6,
    slope_var: float = 1e-7,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run Kalman filter, return (level, slope, slope_var) arrays.

    State x = [level, slope]^T. Transition F = [[1, 1], [0, 1]],
    observation H = [1, 0]. Process noise Q = diag(level_var, slope_var).
    """
    n = len(y)
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.diag([level_var, slope_var])
    R = np.array([[obs_var]])

    x = np.array([[y[0]], [0.0]])
    P = np.eye(2) * 1.0

    levels = np.zeros(n)
    slopes = np.zeros(n)
    slope_vars = np.zeros(n)

    for t in range(n):
        # Predict
        x = F @ x
        P = F @ P @ F.T + Q
        # Update
        z = np.array([[y[t]]])
        innov = z - H @ x
        S = H @ P @ H.T + R
        K = P @ H.T @ np.linalg.inv(S)
        x = x + K @ innov
        P = (np.eye(2) - K @ H) @ P
        levels[t] = float(x[0, 0])
        slopes[t] = float(x[1, 0])
        slope_vars[t] = float(P[1, 1])

    return levels, slopes, slope_vars


class KalmanTrendAlpha(Alpha):
    """Sign-of-slope position with vol-scaled magnitude."""

    DEFAULT_PARAMS = {
        "obs_var": 1e-4,
        "level_var": 1e-6,
        "slope_var": 5e-8,
        "atr_window": 24,
        "z_clip": 3.0,
        # v3.1: per-alpha vol targeting
        "vol_target": 0.40,
        "vol_lookback": 168,
        "vol_cap": 1.5,
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="kalman_trend", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        log_p = np.log(df["close"].astype(float).replace(0, np.nan)).bfill().values
        levels, slopes, svars = _kalman_local_linear(
            log_p,
            obs_var=float(p.get("obs_var", 1e-4)),
            level_var=float(p.get("level_var", 1e-6)),
            slope_var=float(p.get("slope_var", 5e-8)),
        )
        # Slope-to-noise z (slope per bar / sqrt(slope_var))
        z = slopes / np.sqrt(np.maximum(svars, 1e-12))
        z = np.clip(z, -float(p.get("z_clip", 3.0)), float(p.get("z_clip", 3.0)))
        # Squash to [-1, 1] via tanh-like transform
        pos = np.tanh(z * 0.5)
        # Damp by relative ATR (less risk in vol explosions)
        a = atr(df["high"], df["low"], df["close"], period=int(p.get("atr_window", 24)))
        a_norm = (a / df["close"]).clip(0.0, 0.10)
        damp = 1.0 - (a_norm / 0.10).clip(0.0, 1.0) * 0.4
        out = pd.Series(pos, index=df.index) * damp

        # v3.1: per-alpha vol targeting
        if float(p.get("vol_target", 0.0)) > 0:
            vt = vol_target_scale(
                df["close"],
                target_vol_annual=float(p["vol_target"]),
                lookback=int(p["vol_lookback"]),
                cap=float(p["vol_cap"]),
            )
            out = out * vt
        return out.fillna(0.0)
