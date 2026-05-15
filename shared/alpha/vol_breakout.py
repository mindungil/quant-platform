"""Volatility breakout alpha (Keltner / squeeze release).

Detects volatility-compression regimes (Bollinger bands inside Keltner channels
— the "TTM Squeeze") and goes with the direction of the breakout when the
squeeze releases. The MACD histogram sign at release decides direction.

This alpha is intentionally selective: it spends most of the time flat and
fires hard when conditions align.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, atr as compute_atr, ema


class VolBreakoutAlpha(Alpha):
    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_std": 2.0,
        "kc_period": 20,
        "kc_atr_mult": 1.6,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "min_squeeze_bars": 4,    # squeeze must persist this long before release counts
        "hold_bars": 48,          # hold position this many bars after release
        "use_close_breakout": False,  # additionally fire on close breaking the squeeze range
        # Scale position by the MACD histogram magnitude at release,
        # normalized by ATR. Caps keep extreme moves from sizing
        # through the gross-position cap. Set to 0 to restore the
        # legacy behavior of fixed ±1 binary positions.
        "size_by_hist": True,
        "hist_norm_cap": 1.2,
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="vol_breakout", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        high, low, close = df["high"], df["low"], df["close"]

        # Bollinger
        bb_mean = close.rolling(p["bb_period"], min_periods=p["bb_period"]).mean()
        bb_std = close.rolling(p["bb_period"], min_periods=p["bb_period"]).std(ddof=0)
        bb_upper = bb_mean + p["bb_std"] * bb_std
        bb_lower = bb_mean - p["bb_std"] * bb_std

        # Keltner
        kc_mean = ema(close, p["kc_period"])
        atr_v = compute_atr(high, low, close, p["kc_period"])
        kc_upper = kc_mean + p["kc_atr_mult"] * atr_v
        kc_lower = kc_mean - p["kc_atr_mult"] * atr_v

        # Squeeze: Bollinger bands fully inside Keltner channels
        in_squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)

        # MACD histogram for direction at release
        ema_fast = ema(close, p["macd_fast"])
        ema_slow = ema(close, p["macd_slow"])
        macd_line = ema_fast - ema_slow
        macd_signal = ema(macd_line, p["macd_signal"])
        hist = macd_line - macd_signal

        # Hist-to-size ramp normalized by ATR — bigger histograms at
        # release imply stronger directional conviction; unscaled binary
        # ±1 positions dramatically overtrade on weak releases.
        hist_norm = (hist / atr_v.replace(0, np.nan)).clip(
            -p["hist_norm_cap"], p["hist_norm_cap"]
        ).fillna(0.0) / p["hist_norm_cap"]

        # Single O(N) numpy loop (state machine on compressed-break release).
        sq_arr = in_squeeze.to_numpy()
        ph_arr = high.to_numpy()
        pl_arr = low.to_numpy()
        pc_arr = close.to_numpy()
        h_arr = hist.to_numpy()
        hn_arr = hist_norm.to_numpy()
        n = len(pc_arr)

        position = np.zeros(n, dtype=np.float64)
        squeeze_count = 0
        held_for = 0
        held_val = 0.0
        squeeze_high = np.nan
        squeeze_low = np.nan
        size_by_hist = bool(p.get("size_by_hist", True))
        min_sq = int(p["min_squeeze_bars"])
        hold_bars = int(p["hold_bars"])
        use_close_brk = bool(p["use_close_breakout"])

        for i in range(n):
            sq = bool(sq_arr[i]) if not (isinstance(sq_arr[i], float) and np.isnan(sq_arr[i])) else False
            ph = ph_arr[i]; pl = pl_arr[i]; pc = pc_arr[i]
            h = h_arr[i]; hn = hn_arr[i]

            if sq:
                squeeze_count += 1
                squeeze_high = ph if np.isnan(squeeze_high) else max(squeeze_high, ph)
                squeeze_low = pl if np.isnan(squeeze_low) else min(squeeze_low, pl)
            else:
                if squeeze_count >= min_sq and held_for == 0:
                    direction = 0
                    if use_close_brk and not np.isnan(squeeze_high) and not np.isnan(squeeze_low):
                        if pc > squeeze_high:
                            direction = 1
                        elif pc < squeeze_low:
                            direction = -1
                    if direction == 0 and not np.isnan(h):
                        direction = 1 if h > 0 else (-1 if h < 0 else 0)
                    if direction != 0:
                        if size_by_hist and not np.isnan(hn):
                            # hn carries the sign already; enforce it agrees.
                            magnitude = max(abs(hn), 0.25)  # floor so we don't take tiny positions
                        else:
                            magnitude = 1.0
                        held_val = direction * magnitude
                        held_for = hold_bars
                squeeze_count = 0
                squeeze_high = np.nan
                squeeze_low = np.nan

            if held_for > 0:
                position[i] = held_val
                held_for -= 1
                if held_for == 0:
                    held_val = 0.0

        return pd.Series(position, index=close.index).clip(-1.0, 1.0)
