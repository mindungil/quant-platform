"""Multi-period momentum ensemble.

Combines several momentum windows (similar to Asness/Moskowitz time-series
momentum) into a single position signal using equal-weighted z-scores. The
intuition: if 1m, 3m, and 6m returns all agree, you have a strong directional
signal; if they disagree, the position naturally shrinks.

Bar size is inferred from the data; lookback windows are expressed in bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, rolling_zscore


class MomentumEnsembleAlpha(Alpha):
    DEFAULT_PARAMS = {
        # Lookback windows in bars (e.g. for 1h bars: ~3d, ~7d, ~15d, ~30d)
        "windows": [72, 168, 360, 720],
        "vol_window": 168,           # rolling vol window for risk-scaling
        "z_clip": 3.0,
        # Below this, scale *linearly toward zero* (no jump). Previously we
        # hard-clipped below threshold to 0 and floored above to 0.3 which
        # injected a 0→0.3 step every time the signal crossed — a major
        # source of turnover. Smooth ramp keeps total turnover manageable.
        "score_threshold": 0.05,
        "ramp_smoothing": 3,         # EMA span on combined score in bars
        "vol_scale_floor": 0.4,
        "vol_scale_ceiling": 1.5,
        # Bars/year: 24*365 for 24/7 crypto. Equity would be 252*6.5 ≈ 1640.
        "bars_per_year": 24 * 365,
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="momentum_ensemble", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        close = df["close"]
        log_ret = np.log(close).diff()

        # Per-window momentum: cumulative log return / realized vol of that window
        components: list[pd.Series] = []
        for w in p["windows"]:
            cum_ret = log_ret.rolling(w, min_periods=w).sum()
            vol = log_ret.rolling(w, min_periods=w).std(ddof=0) * np.sqrt(w)
            mom = (cum_ret / vol.replace(0, np.nan)).clip(-p["z_clip"], p["z_clip"])
            components.append(mom)

        # Equal-weight combine, then map clipped z to [-1, 1] via /z_clip
        combined = pd.concat(components, axis=1).mean(axis=1) / p["z_clip"]
        combined = combined.clip(-1.0, 1.0).fillna(0.0)

        # Smooth BEFORE thresholding — reduces chatter where the raw
        # score oscillates around the threshold band bar-to-bar.
        if p.get("ramp_smoothing", 1) > 1:
            combined = combined.ewm(span=p["ramp_smoothing"], adjust=False, min_periods=1).mean()

        # Smooth threshold: linearly ramp from 0 at |score|=threshold to
        # full magnitude. Eliminates the 0→min_position jump that
        # previously injected a step change every entry.
        thr = p["score_threshold"]
        excess = combined.abs() - thr
        magnitude = excess.clip(lower=0.0) / max(1.0 - thr, 1e-6)
        shaped = np.sign(combined) * magnitude.clip(0.0, 1.0)

        # Risk-scale by inverse rolling vol (so all assets target similar vol)
        vol = log_ret.rolling(p["vol_window"], min_periods=p["vol_window"]).std(ddof=0)
        # Crypto trades 24/7/365, not 252 trading days. The prior 252*24
        # factor under-annualized by ~30%, which made the vol-target
        # scaling consistently low.
        ann_vol = vol * np.sqrt(p["bars_per_year"])
        target = self.config.target_vol
        scale = (target / ann_vol.replace(0, np.nan)).clip(p["vol_scale_floor"], p["vol_scale_ceiling"]).fillna(1.0)

        return (shaped * scale).clip(-1.0, 1.0)
