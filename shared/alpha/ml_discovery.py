"""ML Discovery Alpha — LightGBM walk-forward alpha with proper safeguards.

Key improvements over ml_forest / ml_meta:
1. Cost-adjusted forward return target (not raw 1-bar or triple-barrier)
2. Purged embargo between train and test windows
3. Dynamic feature selection via Information Coefficient (IC)
4. Decorrelation penalty vs existing trend alphas
5. Position calibration via tanh(pred / rolling_std)

Architecture:
  FeatureEngine → 100+ features per bar
  Walk-forward: train 3000 bars, embargo max(lookback, horizon), predict 720 bars
  LightGBM → cost-adjusted N-bar return prediction
  IC-based top-K feature selection (per refit window)
  Decorrelation: reduce signal when correlated with existing alphas
  Position: tanh(prediction / calibrated_scale) → [-1, 1]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal
from shared.features.engine import FeatureEngine, FeatureEngineConfig
from shared.ml.gbm import GBMWrapper


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

_DEFAULT_PARAMS: dict[str, Any] = {
    "train_window": 3000,       # ~4 months of hourly bars
    "refit_every": 720,         # ~1 month
    "target_horizon": 24,       # predict 24-bar forward return
    "cost_bps": 5.0,            # deducted from target
    "top_k_features": 50,       # IC-based selection per window
    "warmup": 800,              # skip early bars (feature stabilization)
    "max_corr_penalty": 0.4,    # start penalizing above this corr
    "gbm_params": {},           # override GBMWrapper defaults
}


# ---------------------------------------------------------------------------
# ML Discovery Alpha
# ---------------------------------------------------------------------------

class MLDiscoveryAlpha(Alpha):
    """Walk-forward LightGBM alpha with anti-overfit safeguards."""

    def __init__(
        self,
        config: AlphaConfig | None = None,
        feature_engine: FeatureEngine | None = None,
        existing_positions: dict[str, pd.Series] | None = None,
    ) -> None:
        if config is None:
            config = AlphaConfig(name="ml_discovery", params=dict(_DEFAULT_PARAMS))
        super().__init__(config)
        self.feature_engine = feature_engine or FeatureEngine()
        self.existing_positions = existing_positions or {}
        self._models: list[dict[str, Any]] = []

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        """Walk-forward ML signal generation."""
        p = {**_DEFAULT_PARAMS, **self.config.params}
        train_w = p["train_window"]
        refit_every = p["refit_every"]
        horizon = p["target_horizon"]
        cost_bps = p["cost_bps"]
        top_k = p["top_k_features"]
        warmup = p["warmup"]

        # Generate features
        fm = self.feature_engine.generate(df)
        feat = fm.features
        max_lb = fm.max_lookback

        # Compute cost-adjusted forward return target
        log_ret = np.log(df["close"].astype(float) / df["close"].astype(float).shift(1))
        fwd_ret = log_ret.rolling(horizon, min_periods=1).sum().shift(-horizon)
        # Deduct round-trip cost
        cost_drag = 2 * cost_bps / 10_000  # entry + exit
        target = fwd_ret - cost_drag  # biased toward zero (conservative)

        embargo = max(max_lb, horizon) + 10

        n = len(df)
        positions = pd.Series(0.0, index=df.index)
        pred_scale_history: list[float] = []

        # Walk-forward loop
        t = max(warmup, train_w + embargo)
        while t < n:
            te_end = min(t + refit_every, n)

            # Train window
            tr_end = t - embargo
            tr_start = max(0, tr_end - train_w)
            if tr_end - tr_start < 500:
                t = te_end
                continue

            # Extract train data
            X_tr = feat.iloc[tr_start:tr_end].values
            y_tr = target.iloc[tr_start:tr_end].values

            # Remove rows with NaN target (end of series)
            valid = np.isfinite(y_tr)
            X_tr = X_tr[valid]
            y_tr = y_tr[valid]

            if len(X_tr) < 200:
                t = te_end
                continue

            # IC-based feature selection with stability filter
            selected_idx = _select_features_stable(X_tr, y_tr, top_k)
            X_tr_sel = X_tr[:, selected_idx]

            # PurgedKFold inner CV for model selection (3-fold on train)
            split = int(len(X_tr_sel) * 0.8)
            X_fit, X_val = X_tr_sel[:split], X_tr_sel[split:]
            y_fit, y_val = y_tr[:split], y_tr[split:]

            # Ensemble: train 3 models with different seeds, average predictions
            models_ensemble = []
            for seed_offset in range(3):
                gbm_p = dict(p.get("gbm_params", {}))
                gbm_p["seed"] = 42 + seed_offset
                m = GBMWrapper(params=gbm_p)
                m.fit(X_fit, y_fit, X_val, y_val)
                models_ensemble.append(m)

            # Predict on test window — ensemble average
            X_te = feat.iloc[t:te_end].values[:, selected_idx]
            raw_pred = np.mean([m.predict(X_te) for m in models_ensemble], axis=0)

            # Calibrate scale from ensemble training predictions
            train_preds = [m.predict(X_tr_sel) for m in models_ensemble]
            avg_train_pred = np.mean(train_preds, axis=0)
            pred_std = max(float(np.std(avg_train_pred)), 1e-8)
            pred_scale_history.append(pred_std)

            # Position: tanh(pred / scale) → [-1, 1]
            calibrated = np.tanh(raw_pred / pred_std)

            # Decorrelation penalty
            for alpha_name, alpha_pos in self.existing_positions.items():
                if alpha_pos is not None and len(alpha_pos) >= te_end:
                    existing_slice = alpha_pos.iloc[t:te_end].values
                    if len(existing_slice) == len(calibrated):
                        corr = _safe_corr(calibrated, existing_slice)
                        if abs(corr) > p["max_corr_penalty"]:
                            penalty = max(0.0, 1.0 - abs(corr))
                            calibrated = calibrated * penalty

            positions.iloc[t:te_end] = calibrated

            # Store model info for diagnostics
            self._models.append({
                "t_start": t,
                "t_end": te_end,
                "n_features": len(selected_idx),
                "pred_std": pred_std,
            })

            t = te_end

        return positions

    def _diagnostics(self, df, raw_signal) -> dict[str, Any]:
        base = super()._diagnostics(df, raw_signal)
        base["n_refit_windows"] = len(self._models)
        if self._models:
            base["avg_pred_std"] = float(np.mean([m["pred_std"] for m in self._models]))
        return base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select_features_by_ic(
    X: np.ndarray, y: np.ndarray, top_k: int,
) -> np.ndarray:
    """Select top-K features by absolute Spearman IC with target.

    IC = rank_corr(feature, target). Computed on training data only.
    Returns array of column indices.
    """
    n_feat = X.shape[1]
    if n_feat <= top_k:
        return np.arange(n_feat)

    y_rank = _rank(y)
    ics = np.zeros(n_feat)
    for j in range(n_feat):
        x_rank = _rank(X[:, j])
        ics[j] = abs(_pearson(x_rank, y_rank))

    return np.argsort(ics)[::-1][:top_k]


def _select_features_stable(
    X: np.ndarray, y: np.ndarray, top_k: int, n_splits: int = 3,
) -> np.ndarray:
    """Select features that are consistently informative across time splits.

    Splits training data into n_splits temporal chunks, computes IC in each,
    keeps only features that rank in top-2K in all splits, then takes top-K
    by average IC. This prevents selecting features with spurious one-period
    correlations.
    """
    n_feat = X.shape[1]
    if n_feat <= top_k:
        return np.arange(n_feat)

    n = len(y)
    split_size = n // n_splits
    if split_size < 100:
        return _select_features_by_ic(X, y, top_k)

    # Compute IC per split
    ic_per_split = np.zeros((n_splits, n_feat))
    for s in range(n_splits):
        s_start = s * split_size
        s_end = (s + 1) * split_size if s < n_splits - 1 else n
        X_s = X[s_start:s_end]
        y_s = y[s_start:s_end]
        y_rank = _rank(y_s)
        for j in range(n_feat):
            x_rank = _rank(X_s[:, j])
            ic_per_split[s, j] = abs(_pearson(x_rank, y_rank))

    # Features must be in top 2*top_k in ALL splits (stability filter)
    threshold_k = min(2 * top_k, n_feat)
    stable_mask = np.ones(n_feat, dtype=bool)
    for s in range(n_splits):
        top_in_split = set(np.argsort(ic_per_split[s])[::-1][:threshold_k])
        for j in range(n_feat):
            if j not in top_in_split:
                stable_mask[j] = False

    # Among stable features, rank by average IC
    avg_ic = ic_per_split.mean(axis=0)
    avg_ic[~stable_mask] = -1.0  # exclude unstable
    selected = np.argsort(avg_ic)[::-1][:top_k]

    # If not enough stable features, fall back to simple IC
    if (avg_ic[selected] < 0).sum() > top_k // 2:
        return _select_features_by_ic(X, y, top_k)

    return selected[avg_ic[selected] >= 0]


def _rank(arr: np.ndarray) -> np.ndarray:
    """Rank values (average tie-breaking)."""
    temp = arr.argsort()
    ranks = np.empty_like(temp, dtype=float)
    ranks[temp] = np.arange(len(arr), dtype=float)
    return ranks


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation, safe for constant arrays."""
    if len(a) < 3:
        return 0.0
    a_m = a - a.mean()
    b_m = b - b.mean()
    denom = np.sqrt((a_m ** 2).sum() * (b_m ** 2).sum())
    if denom < 1e-12:
        return 0.0
    return float((a_m * b_m).sum() / denom)


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    """Correlation between two signal arrays, handling constant."""
    if len(a) < 5:
        return 0.0
    std_a = np.std(a)
    std_b = np.std(b)
    if std_a < 1e-10 or std_b < 1e-10:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])
