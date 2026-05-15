"""ML Meta-Alpha: confidence-weighted ensemble booster.

Instead of generating standalone signals, this alpha BOOSTS existing
alpha ensemble signals by predicting their confidence.

Key insight from López de Prado (AFML Ch. 3):
  "Don't ask ML to predict returns. Ask it: given that the primary
   model says BUY, what's the probability the trade works?"

Architecture:
  1. Primary signal: ensemble of existing 3 trend alphas (direction)
  2. Features: 118+ from FeatureEngine (market state)
  3. Target: did the primary signal direction produce positive cost-adjusted return?
  4. Model: LightGBM classifier → P(correct) in [0, 1]
  5. Output: primary_position × P(correct) → boosted position

Why this works when standalone ML fails:
  - Binary target (correct/wrong) has MUCH higher signal than raw return
  - Conditional: only needs to predict when existing alphas work, not market direction
  - Fewer trades: high confidence → trade, low confidence → reduce position
"""
from __future__ import annotations

from dataclasses import field
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal
from shared.features.engine import FeatureEngine
from shared.ml.gbm import GBMWrapper


_DEFAULT_PARAMS: dict[str, Any] = {
    "train_window": 4000,
    "refit_every": 720,
    "target_horizon": 24,
    "cost_bps": 5.0,
    "top_k_features": 40,
    "warmup": 1200,
    "confidence_floor": 0.3,   # below this → zero position
    "confidence_scale": 2.0,   # multiply confidence by this for position sizing
}


class MLMetaAlpha(Alpha):
    """Meta-labeling alpha that boosts existing signals with ML confidence."""

    def __init__(
        self,
        config: AlphaConfig | None = None,
        primary_positions: pd.Series | None = None,
        feature_engine: FeatureEngine | None = None,
    ) -> None:
        if config is None:
            config = AlphaConfig(name="ml_meta_alpha", params=dict(_DEFAULT_PARAMS))
        super().__init__(config)
        self.primary_positions = primary_positions
        self.feature_engine = feature_engine or FeatureEngine()

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = {**_DEFAULT_PARAMS, **self.config.params}

        if self.primary_positions is None or self.primary_positions.abs().sum() < 1:
            return pd.Series(0.0, index=df.index)

        primary = self.primary_positions.reindex(df.index).fillna(0.0)

        # Generate features
        fm = self.feature_engine.generate(df)
        feat = fm.features
        max_lb = fm.max_lookback

        # Build meta-label target:
        # 1 if primary direction was correct (produced positive cost-adjusted return)
        # 0 otherwise
        close = df["close"].astype(float)
        log_ret = np.log(close / close.shift(1)).fillna(0)
        horizon = p["target_horizon"]
        cost_bps = p["cost_bps"]

        fwd_ret = log_ret.rolling(horizon, min_periods=1).sum().shift(-horizon)
        cost_drag = 2 * cost_bps / 10_000

        # Direction-adjusted return: positive when primary signal was right
        primary_sign = np.sign(primary)
        direction_ret = primary_sign * fwd_ret - cost_drag
        target = (direction_ret > 0).astype(float)  # binary: 1=correct, 0=wrong

        # Walk-forward
        n = len(df)
        embargo = max(max_lb, horizon) + 10
        train_w = p["train_window"]
        refit_every = p["refit_every"]
        warmup = p["warmup"]
        top_k = p["top_k_features"]
        conf_floor = p["confidence_floor"]
        conf_scale = p["confidence_scale"]

        confidence = pd.Series(0.5, index=df.index)  # default 50% confidence

        t = max(warmup, train_w + embargo)
        while t < n:
            te_end = min(t + refit_every, n)
            tr_end = t - embargo
            tr_start = max(0, tr_end - train_w)

            if tr_end - tr_start < 500:
                t = te_end
                continue

            X_tr = feat.iloc[tr_start:tr_end].values
            y_tr = target.iloc[tr_start:tr_end].values

            # Only train on bars where primary had a position
            has_pos = np.abs(primary.iloc[tr_start:tr_end].values) > 0.05
            valid = np.isfinite(y_tr) & has_pos
            X_tr = X_tr[valid]
            y_tr = y_tr[valid]

            if len(X_tr) < 200 or y_tr.sum() < 50 or (1 - y_tr).sum() < 50:
                t = te_end
                continue

            # Feature selection
            selected_idx = _select_top_features(X_tr, y_tr, top_k)
            X_tr_sel = X_tr[:, selected_idx]

            # Train LightGBM for binary classification
            split = int(len(X_tr_sel) * 0.8)
            model = GBMWrapper(params={
                "objective": "binary",
                "metric": "binary_logloss",
                "learning_rate": 0.03,
                "num_leaves": 15,      # shallower = less overfit
                "max_depth": 4,
                "min_child_samples": 50,
                "subsample": 0.7,
                "colsample_bytree": 0.7,
                "reg_alpha": 0.5,
                "reg_lambda": 2.0,
                "n_estimators": 150,
            })
            model.fit(X_tr_sel[:split], y_tr[:split], X_tr_sel[split:], y_tr[split:])

            # Predict confidence on test window
            X_te = feat.iloc[t:te_end].values[:, selected_idx]
            raw_conf = model.predict(X_te)  # probability in [0, 1] for binary

            # Sigmoid calibration (LightGBM binary outputs are already probabilities)
            # Clip to [0, 1]
            raw_conf = np.clip(raw_conf, 0, 1)

            confidence.iloc[t:te_end] = raw_conf
            t = te_end

        # Build final position: primary × (confidence - floor) × scale
        # High confidence → amplify, low confidence → suppress
        adj_confidence = (confidence - conf_floor).clip(lower=0) * conf_scale
        adj_confidence = adj_confidence.clip(upper=1.5)  # cap

        final_pos = primary * adj_confidence

        return final_pos.clip(-1, 1)


def _select_top_features(X: np.ndarray, y: np.ndarray, top_k: int) -> np.ndarray:
    """Select features by mutual information proxy (point-biserial correlation)."""
    n_feat = X.shape[1]
    if n_feat <= top_k:
        return np.arange(n_feat)

    scores = np.zeros(n_feat)
    for j in range(n_feat):
        x = X[:, j]
        # Point-biserial: mean(x|y=1) - mean(x|y=0) normalized
        mask_1 = y > 0.5
        mask_0 = ~mask_1
        if mask_1.sum() < 10 or mask_0.sum() < 10:
            continue
        mean_1 = np.mean(x[mask_1])
        mean_0 = np.mean(x[mask_0])
        pooled_std = np.std(x)
        if pooled_std > 1e-10:
            scores[j] = abs(mean_1 - mean_0) / pooled_std

    return np.argsort(scores)[::-1][:top_k]
