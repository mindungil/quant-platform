"""Triple Barrier Method (López de Prado AFML Ch. 3).

For each event time t0, walk forward up to vertical-barrier H bars, and
record which barrier is touched first:
  +1 if upper profit-take (PT) is hit first
  -1 if lower stop-loss (SL) is hit first
   0 if vertical barrier is hit first
The PT/SL widths are typically scaled by realized volatility, so the
labels are comparable across regimes.

Meta-labeling: take a primary signal that decides direction (e.g. trend
breakout). The triple-barrier label conditional on that direction (i.e.
'did the trade make money?') becomes a binary target. A secondary ML
classifier learns when to size up or skip the primary signal — far more
sample-efficient than learning direction from scratch.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TripleBarrierLabel:
    """Output of triple barrier labeling."""
    bin: pd.Series   # ∈ {-1, 0, +1}: which barrier hit first
    ret: pd.Series   # log return at touch
    t1: pd.Series    # touch timestamp for each event


def daily_vol(close: pd.Series, span: int = 100) -> pd.Series:
    """EWMA stdev of log returns — used to scale barriers."""
    log_ret = np.log(close / close.shift(1)).fillna(0.0)
    return log_ret.ewm(span=span, adjust=False, min_periods=max(2, span // 4)).std().fillna(0.0)


def triple_barrier_labels(
    close: pd.Series,
    events: pd.DatetimeIndex | pd.Index,
    pt_mult: float = 2.0,
    sl_mult: float = 2.0,
    vertical: int = 24,
    vol: pd.Series | None = None,
    side: pd.Series | None = None,
) -> TripleBarrierLabel:
    """Compute triple-barrier labels for each event.

    Args:
        close: price series indexed by time
        events: timestamps where a position would be opened
        pt_mult: profit-take multiplier on volatility
        sl_mult: stop-loss multiplier on volatility
        vertical: vertical barrier (max bars to hold)
        vol: volatility series (default: EWMA daily_vol)
        side: optional ±1 series at each event time. If given, PT/SL are
              applied in the direction of `side` (meta-labeling mode); the
              returned `bin` is binary {0, 1} where 1 = trade made money.
    """
    if vol is None:
        vol = daily_vol(close, span=100)
    close = close.astype(float)
    events = pd.Index(events)
    events = events[events.isin(close.index)]
    n = len(close)
    pos_lookup = {ts: i for i, ts in enumerate(close.index)}
    bins = []
    rets = []
    touches = []
    cls_vals = close.values
    for ts in events:
        i0 = pos_lookup.get(ts)
        if i0 is None or i0 >= n - 1:
            continue
        v0 = float(vol.iloc[i0]) if i0 < len(vol) else 0.0
        if not np.isfinite(v0) or v0 <= 0:
            v0 = 1e-4
        end = min(n - 1, i0 + vertical)
        p0 = cls_vals[i0]
        side_val = float(side.iloc[i0]) if (side is not None and i0 < len(side)) else 1.0
        if side_val == 0:
            continue
        upper = p0 * np.exp(pt_mult * v0 * (1 if side_val > 0 else -1) * (1 if side_val > 0 else 1))
        lower = p0 * np.exp(-sl_mult * v0 * (1 if side_val > 0 else -1) * (1 if side_val > 0 else 1))
        # Re-derive cleanly: with side, profit means price moved in direction of side
        if side_val > 0:
            pt_price = p0 * np.exp(pt_mult * v0)
            sl_price = p0 * np.exp(-sl_mult * v0)
        else:
            pt_price = p0 * np.exp(-pt_mult * v0)
            sl_price = p0 * np.exp(sl_mult * v0)

        b = 0
        touch_idx = end
        for j in range(i0 + 1, end + 1):
            pj = cls_vals[j]
            if side_val > 0:
                if pj >= pt_price:
                    b = 1
                    touch_idx = j
                    break
                if pj <= sl_price:
                    b = -1
                    touch_idx = j
                    break
            else:
                if pj <= pt_price:
                    b = 1
                    touch_idx = j
                    break
                if pj >= sl_price:
                    b = -1
                    touch_idx = j
                    break
        log_ret = float(np.log(cls_vals[touch_idx] / p0)) * side_val
        if side is not None:
            # meta-label binary
            bin_val = 1 if log_ret > 0 else 0
        else:
            bin_val = b
        bins.append(bin_val)
        rets.append(log_ret)
        touches.append(close.index[touch_idx])

    valid_events = events[: len(bins)]  # events that produced a label
    return TripleBarrierLabel(
        bin=pd.Series(bins, index=valid_events, dtype=float),
        ret=pd.Series(rets, index=valid_events, dtype=float),
        t1=pd.Series(touches, index=valid_events),
    )


def apply_meta_label(
    primary_signal: pd.Series,
    meta_proba: pd.Series,
    threshold: float = 0.55,
) -> pd.Series:
    """Combine a primary direction with a secondary 'should I trade' probability.

    Returns a position series:
        size = primary_signal where meta_proba > threshold, else 0
        scaled by (meta_proba - threshold) / (1 - threshold) so confident
        bets get full size and marginal bets get small size.
    """
    aligned = meta_proba.reindex(primary_signal.index).fillna(0.5)
    confidence = ((aligned - threshold) / (1.0 - threshold)).clip(0.0, 1.0)
    return primary_signal * confidence
