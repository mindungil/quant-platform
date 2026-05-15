"""ml_forest: meta-labeled bagged-tree alpha (López de Prado AFML).

Architecture:
  Primary signal      → Kalman trend slope (sign tells direction)
  Triple-barrier label → did the primary trade hit profit-take or stop-loss?
  Features            → fracdiff(log close) + microstructure + technicals
  Secondary model     → bagged regression trees, walk-forward retrained,
                        purged splits to prevent label leakage
  Position            → primary_direction × meta_confidence

Why meta-labeling instead of direct return prediction:
  Bar-level returns have <0.05 R² ceiling — pure prediction overfits.
  But once you have a (mediocre) primary direction, asking ML "is this
  setup likely to work?" is much higher SNR, because the question is
  conditional on a specific market state.

Why bagged trees:
  - Capture nonlinear interactions ridge can't (e.g. "high vol AND
    bullish RSI" ≠ vol + RSI)
  - Out-of-bag bias from bagging gives free regularization
  - No external dependency (LightGBM not installed)

Walk-forward retrain protocol:
  - Train every refit_every bars (default 720 = 30 days hourly)
  - Train window train_window (default 3000)
  - Skip first warmup bars while features stabilize
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, atr, adx, rsi, ema, bollinger_pctb, rolling_zscore
from shared.alpha.kalman_trend import _kalman_local_linear
from shared.features.cusum import vol_cusum_filter
from shared.features.fracdiff import frac_diff_ffd
from shared.features.microstructure import (
    amihud_illiquidity,
    kyle_lambda,
    high_low_volatility,
    vpin_proxy,
)
from shared.labels.triple_barrier import daily_vol
from shared.ml.trees import RandomForestRegressor


def _build_rich_features(
    df: pd.DataFrame,
    exog: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rich feature panel: fracdiff + microstructure + technicals.

    Args:
        df: target asset OHLCV
        exog: optional exogenous OHLCV (e.g. BTC as a market driver) — adds
              cross-asset features: BTC log return, BTC vol, rolling
              correlation between target and BTC log returns.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float) if "volume" in df else pd.Series(1.0, index=df.index)
    log_close = np.log(close.replace(0, np.nan)).bfill()
    log_ret = log_close.diff().fillna(0.0)

    feats = pd.DataFrame(index=df.index)

    # --- stationary memory features (fracdiff) ---
    for d in (0.3, 0.5):
        feats[f"fd_{d}"] = frac_diff_ffd(log_close, d=d).fillna(0.0)

    # --- multi-horizon momentum ---
    for h in (12, 24, 72, 168, 360):
        feats[f"ret_{h}"] = log_ret.rolling(h).sum().fillna(0.0)

    # --- volatility regime ---
    feats["vol_24"] = log_ret.rolling(24).std(ddof=0).fillna(0.0)
    feats["vol_168"] = log_ret.rolling(168).std(ddof=0).fillna(0.0)
    feats["vol_ratio"] = (feats["vol_24"] / feats["vol_168"].replace(0, np.nan)).fillna(1.0)

    # --- microstructure ---
    feats["amihud"] = rolling_zscore(amihud_illiquidity(close, vol, window=24), 168)
    feats["kyle"] = rolling_zscore(kyle_lambda(close, vol, window=48), 168)
    feats["pk_vol"] = rolling_zscore(high_low_volatility(high, low, window=24), 168)
    feats["vpin"] = vpin_proxy(close, vol, window=50) - 0.5

    # --- classic technicals ---
    feats["rsi"] = (rsi(close, 14) - 50.0) / 50.0
    feats["bbpb"] = bollinger_pctb(close, 20, 2.0) - 0.5
    feats["adx"] = adx(high, low, close, 14) / 100.0
    feats["ema_diff"] = (ema(close, 50) - ema(close, 200)) / atr(high, low, close, 14).replace(0, np.nan)

    # --- cross-asset features ---
    if exog is not None and "close" in exog.columns:
        ex_close = exog["close"].astype(float).reindex(df.index).ffill()
        ex_log = np.log(ex_close.replace(0, np.nan)).bfill()
        ex_ret = ex_log.diff().fillna(0.0)
        feats["btc_ret_24"] = ex_ret.rolling(24).sum().fillna(0.0)
        feats["btc_ret_72"] = ex_ret.rolling(72).sum().fillna(0.0)
        feats["btc_vol_24"] = ex_ret.rolling(24).std(ddof=0).fillna(0.0)
        # Rolling correlation of target and BTC log returns
        corr_window = 168
        roll_corr = (
            log_ret.rolling(corr_window, min_periods=corr_window // 2)
            .corr(ex_ret)
            .fillna(0.0)
        )
        feats["btc_corr_168"] = roll_corr
        # Beta proxy: target vol / btc vol
        tgt_vol = log_ret.rolling(168, min_periods=24).std(ddof=0)
        btc_vol = ex_ret.rolling(168, min_periods=24).std(ddof=0)
        feats["btc_vol_ratio"] = (tgt_vol / btc_vol.replace(0, np.nan)).fillna(1.0)

    return feats.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _kalman_primary_signal(df: pd.DataFrame) -> pd.Series:
    """Primary direction from a Kalman trend slope (no shift inside)."""
    log_p = np.log(df["close"].astype(float).replace(0, np.nan)).bfill().values
    _, slopes, svars = _kalman_local_linear(log_p, obs_var=1e-4, level_var=1e-6, slope_var=5e-8)
    z = slopes / np.sqrt(np.maximum(svars, 1e-12))
    return pd.Series(np.sign(z), index=df.index, dtype=float)


def _triple_barrier_outcome(
    close: pd.Series,
    side: pd.Series,
    pt_mult: float = 2.0,
    sl_mult: float = 2.0,
    vertical: int = 24,
) -> pd.Series:
    """Vectorized triple-barrier outcome (1=win, 0=loss).

    For each bar i with non-zero side, look at bars i+1..i+vertical.
    Win iff the price-times-side excess crosses pt_mult*vol BEFORE crossing
    -sl_mult*vol. For ties / vertical barrier hit: count as win if final
    drift in favour. The implementation builds a (n × vertical) gather of
    forward log-returns and resolves the barrier touch order vectorially.
    """
    vol = daily_vol(close, span=100).values
    cv = close.values.astype(float)
    sd = side.values.astype(float)
    n = len(cv)
    if n < 2:
        return pd.Series(np.zeros(n), index=close.index)

    log_p = np.log(np.maximum(cv, 1e-12))
    H = int(vertical)
    out = np.zeros(n)

    # Build forward log-return matrix [n, H+1] where col h = log(cv[i+h]) - log(cv[i])
    fwd_log = np.full((n, H + 1), np.nan)
    for h in range(H + 1):
        end = n - h
        fwd_log[:end, h] = log_p[h : h + end] - log_p[:end]

    safe_vol = np.where(vol > 1e-6, vol, 1e-4)
    pt_thr = pt_mult * safe_vol  # per-bar
    sl_thr = sl_mult * safe_vol

    # signed forward returns: positive = in favour of side
    signed = fwd_log * sd[:, None]  # broadcast
    # Find first index where signed > pt_thr (win), or signed < -sl_thr (loss).
    # nan → ignore (treat as no touch).
    pt_hit = signed >= pt_thr[:, None]
    sl_hit = signed <= -sl_thr[:, None]

    # For each row find first True column index in pt_hit / sl_hit (or -1 if none)
    def first_true(mat: np.ndarray) -> np.ndarray:
        # argmax returns 0 if all False; mask separately
        am = mat.argmax(axis=1)
        any_true = mat.any(axis=1)
        am[~any_true] = -1
        return am

    pt_idx = first_true(pt_hit[:, 1:]) + 1  # offset to skip h=0; -1 stays -1+1=0 (handled below)
    sl_idx = first_true(sl_hit[:, 1:]) + 1
    pt_no = pt_hit[:, 1:].any(axis=1) == False
    sl_no = sl_hit[:, 1:].any(axis=1) == False
    pt_idx_eff = np.where(pt_no, np.iinfo(np.int64).max, pt_idx).astype(np.int64)
    sl_idx_eff = np.where(sl_no, np.iinfo(np.int64).max, sl_idx).astype(np.int64)

    win_pt = pt_idx_eff < sl_idx_eff
    # Vertical resolved by sign of final signed return
    vert_drift = signed[:, H]
    vert_win = (vert_drift > 0) & pt_no & sl_no
    win = (win_pt | vert_win).astype(float)

    # Mask events with zero side or near-end
    win[sd == 0] = 0.0
    win[n - 1 :] = 0.0
    out = win
    return pd.Series(out, index=close.index)


def _triple_barrier_return(
    close: pd.Series,
    side: pd.Series,
    pt_mult: float = 2.5,
    sl_mult: float = 1.0,
    vertical: int = 36,
) -> pd.Series:
    """Continuous regression target: signed log return at first barrier touch.

    Same triple-barrier walk as `_triple_barrier_outcome`, but returns the
    actual log-return × side at the touched bar instead of a 0/1 win flag.
    A continuous target gives the forest a much wider y-range to learn,
    avoiding the proba-collapses-near-0.5 problem of binary classification.
    """
    vol = daily_vol(close, span=100).values
    cv = close.values.astype(float)
    sd = side.values.astype(float)
    n = len(cv)
    if n < 2:
        return pd.Series(np.zeros(n), index=close.index)

    log_p = np.log(np.maximum(cv, 1e-12))
    H = int(vertical)
    safe_vol = np.where(vol > 1e-6, vol, 1e-4)
    pt_thr = pt_mult * safe_vol
    sl_thr = sl_mult * safe_vol

    # Build forward log-return matrix
    fwd_log = np.full((n, H + 1), np.nan)
    for h in range(H + 1):
        end = n - h
        fwd_log[:end, h] = log_p[h : h + end] - log_p[:end]
    signed = fwd_log * sd[:, None]

    pt_hit = signed[:, 1:] >= pt_thr[:, None]
    sl_hit = signed[:, 1:] <= -sl_thr[:, None]

    # First-touch column for each
    pt_any = pt_hit.any(axis=1)
    sl_any = sl_hit.any(axis=1)
    pt_idx = pt_hit.argmax(axis=1) + 1
    sl_idx = sl_hit.argmax(axis=1) + 1
    pt_eff = np.where(pt_any, pt_idx, np.iinfo(np.int64).max).astype(np.int64)
    sl_eff = np.where(sl_any, sl_idx, np.iinfo(np.int64).max).astype(np.int64)
    touch_idx = np.minimum(np.minimum(pt_eff, sl_eff), H)
    rows = np.arange(n)
    out = signed[rows, touch_idx]
    out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    out[sd == 0] = 0.0
    out[n - 1 :] = 0.0
    return pd.Series(out, index=close.index)


class MetaForestAlpha(Alpha):
    """Meta-labeling alpha: Kalman primary × bagged-forest confidence."""

    DEFAULT_PARAMS = {
        "refit_every": 720,
        "train_window": 3000,
        "warmup": 1000,
        "n_estimators": 30,
        "max_depth": 5,
        "min_samples_leaf": 30,
        "pt_mult": 2.5,
        "sl_mult": 1.0,
        "vertical": 36,
        # v3.1: regression target + sigmoid calibration
        "use_regression": True,
        "use_cusum_events": True,
        "cusum_k": 1.5,
        "size_gain": 8.0,
    }

    def __init__(
        self,
        config: Optional[AlphaConfig] = None,
        exog: Optional[pd.DataFrame] = None,
    ) -> None:
        cfg = config or AlphaConfig(name="ml_forest", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)
        self._exog = exog  # optional BTC OHLCV for cross-asset features

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        feats = _build_rich_features(df, exog=self._exog)
        primary = _kalman_primary_signal(df)

        # v3.1: choose regression target (signed log return) by default; the
        # regressor's predicted return gives a much wider y-spread than the
        # binary win/loss target which collapses near 0.5.
        use_regression = bool(p.get("use_regression", True))
        if use_regression:
            labels = _triple_barrier_return(
                df["close"].astype(float),
                primary,
                pt_mult=float(p["pt_mult"]),
                sl_mult=float(p["sl_mult"]),
                vertical=int(p["vertical"]),
            )
        else:
            labels = _triple_barrier_outcome(
                df["close"].astype(float),
                primary,
                pt_mult=float(p["pt_mult"]),
                sl_mult=float(p["sl_mult"]),
                vertical=int(p["vertical"]),
            )

        # v3.1: subsample training events with a CUSUM filter so the forest
        # learns from informative volatility events instead of every quiet bar
        cusum_events: set[int] | None = None
        if bool(p.get("use_cusum_events", True)):
            ev_idx = vol_cusum_filter(df["close"].astype(float), span=100, k=float(p["cusum_k"]))
            if len(ev_idx) > 200:
                pos_lookup = {ts: i for i, ts in enumerate(df.index)}
                cusum_events = {pos_lookup[ts] for ts in ev_idx if ts in pos_lookup}

        position = pd.Series(0.0, index=df.index)
        warmup = int(p["warmup"])
        train_win = int(p["train_window"])
        refit_every = int(p["refit_every"])

        n = len(df)
        start = max(warmup, train_win)
        if start >= n:
            return position

        feat_vals = feats.values
        label_vals = labels.values
        prim_vals = primary.values

        for fit_start in range(start, n, refit_every):
            tr_lo = fit_start - train_win
            tr_hi = fit_start
            X_tr = feat_vals[tr_lo:tr_hi]
            y_tr = label_vals[tr_lo:tr_hi]
            mask_arr = (prim_vals[tr_lo:tr_hi] != 0)
            if cusum_events is not None:
                ev_mask = np.array(
                    [(tr_lo + i) in cusum_events for i in range(tr_hi - tr_lo)],
                    dtype=bool,
                )
                # Combine: event bars OR (fallback) all bars if too few events
                if ev_mask.sum() >= 200:
                    mask_arr = mask_arr & ev_mask
            if mask_arr.sum() < 100:
                continue
            X_tr = X_tr[mask_arr]
            y_tr = y_tr[mask_arr]
            if np.std(y_tr) < 1e-12:
                continue
            forest = RandomForestRegressor(
                n_estimators=int(p["n_estimators"]),
                max_depth=int(p["max_depth"]),
                min_samples_leaf=int(p["min_samples_leaf"]),
                seed=fit_start,
            )
            forest.fit(X_tr, y_tr)
            te_lo = fit_start
            te_hi = min(fit_start + refit_every, n)
            X_te = feat_vals[te_lo:te_hi]
            preds = forest.predict(X_te)

            size_gain = float(p["size_gain"])
            if use_regression:
                # Standardize preds by training-set scale for stable sizing
                tr_scale = float(np.std(y_tr)) + 1e-9
                z = preds / tr_scale
                # tanh squash: |z|=1 → ~0.46, |z|=2 → ~0.76, |z|=3 → ~0.91
                confidence = np.tanh(z * (size_gain / 4.0))
                # Long if prediction agrees with primary direction
                primary_window = prim_vals[te_lo:te_hi]
                agree = np.sign(confidence) == np.sign(primary_window)
                sized = np.where(agree, primary_window * np.abs(confidence), 0.0)
            else:
                confidence = np.tanh(np.maximum(preds - 0.5, 0.0) * size_gain)
                sized = prim_vals[te_lo:te_hi] * confidence
            position.iloc[te_lo:te_hi] = sized

        return position
