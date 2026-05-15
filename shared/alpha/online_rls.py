"""Online RLS alpha — continuously-trained adaptive linear model.

A 'time-series model that learns continuously without GPU':
  - Recursive Least Squares with exponential forgetting (λ ≈ 0.999)
  - Updates weights on every bar in O(d²) — pure CPU, ~10 microseconds
  - Effective memory horizon = 1 / (1 - λ); λ=0.999 → ~1000 bars (~6 weeks)
  - Predicts the H-bar-ahead log return; sizes via tanh of z-score

This is the simplest possible 'continuously trained' model that actually
adapts to regime drift. It's deliberately linear so the per-step update
stays cheap and stable. Competing against the bagged forest (which retrains
every 720 bars), the RLS variant adapts daily to new data.

References:
  Haykin, Adaptive Filter Theory, 4th ed., Ch. 14 (RLS).
  López de Prado, AFML, Ch. 6 (online learning intuition).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig
from shared.alpha.ml_forest import _build_rich_features
from shared.ml.online import RecursiveLeastSquares


class OnlineRLSAlpha(Alpha):
    """Continuously-trained adaptive linear alpha."""

    DEFAULT_PARAMS = {
        "warmup": 500,
        "forgetting": 0.999,
        "init_var": 100.0,
        "target_horizon": 24,      # predict H-bar-ahead log return
        "size_gain": 6.0,
        "z_clip": 3.0,
    }

    def __init__(
        self,
        config: Optional[AlphaConfig] = None,
        exog: Optional[pd.DataFrame] = None,
    ) -> None:
        cfg = config or AlphaConfig(name="online_rls", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)
        self._exog = exog

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        feats = _build_rich_features(df, exog=self._exog)
        n_features = feats.shape[1] + 1  # +1 for bias
        H = int(p["target_horizon"])
        warmup = int(p["warmup"])
        size_gain = float(p["size_gain"])
        z_clip = float(p["z_clip"])

        close = df["close"].astype(float)
        log_close = np.log(close.replace(0, np.nan)).bfill().values
        # Future log return target — we can only TRAIN on bars where we've
        # actually observed t+H, but we PREDICT the model output every bar.
        target = np.zeros(len(close))
        target[: -H] = log_close[H:] - log_close[: -H]

        rls = RecursiveLeastSquares(
            n_features=n_features,
            forgetting=float(p["forgetting"]),
            init_var=float(p["init_var"]),
        )

        feat_vals = feats.values
        n = len(feat_vals)
        position = np.zeros(n)
        # Running prediction MAGNITUDE scale (no centering — sign matters)
        run_n = 0
        run_abs = 0.0  # rolling mean |pred|

        for i in range(n):
            x = np.concatenate([feat_vals[i], [1.0]])  # bias
            pred = rls.predict(x)

            if i >= warmup:
                run_n += 1
                run_abs += (abs(pred) - run_abs) / run_n
                if run_abs > 1e-9:
                    z = pred / run_abs  # prediction in units of typical magnitude
                else:
                    z = 0.0
                z = float(np.clip(z, -z_clip, z_clip))
                position[i] = float(np.tanh(z * size_gain / z_clip))

            if i >= H:
                hist_x = np.concatenate([feat_vals[i - H], [1.0]])
                hist_y = target[i - H]
                rls.update(hist_x, hist_y)

        return pd.Series(position, index=df.index)
