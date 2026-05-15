"""ML meta-alpha: online ridge regression over factor + alpha signals.

Takes a panel of feature signals (other alpha outputs + raw indicator
factors) and learns a weight vector that predicts the next-bar return.
The position is then `tanh(prediction / scale)`.

Properties:
- Pure numpy — no sklearn / lightgbm dependency
- Walk-forward retrained: weights refit every `refit_every` bars on the
  trailing window. Out-of-sample by construction, no leakage.
- Ridge regularization handles colinear features
- Optional feature standardization (rolling)
- Honest no-op when feature panel is empty

Why an online linear model and not a transformer:
- Crypto bar-level signal is mostly captured by linear combinations of
  technical features; bigger models overfit
- Walk-forward retraining is cheap (closed-form ridge solve)
- Auditable — you can read the weight vector and understand what it does

References:
- Rasmussen & Williams §2.1 (closed-form ridge)
- López de Prado, "Advances in Financial ML" §6 (purged walk-forward CV)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig


@dataclass
class _RidgeFit:
    weights: np.ndarray   # shape (n_features,)
    bias: float
    feature_means: np.ndarray
    feature_stds: np.ndarray


def _ridge_fit(X: np.ndarray, y: np.ndarray, alpha: float) -> _RidgeFit:
    """Closed-form ridge: w = (X'X + αI)⁻¹ X'y, with feature standardization."""
    means = X.mean(axis=0)
    stds = X.std(axis=0, ddof=0)
    stds = np.where(stds > 1e-9, stds, 1.0)
    Xs = (X - means) / stds
    n, d = Xs.shape
    A = Xs.T @ Xs + alpha * np.eye(d)
    b = Xs.T @ (y - y.mean())
    try:
        w = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        w = np.zeros(d)
    return _RidgeFit(weights=w, bias=float(y.mean()), feature_means=means, feature_stds=stds)


def _ridge_predict(fit: _RidgeFit, X: np.ndarray) -> np.ndarray:
    Xs = (X - fit.feature_means) / fit.feature_stds
    return Xs @ fit.weights + fit.bias


# ---- feature builder ----


def default_feature_builder(df: pd.DataFrame) -> pd.DataFrame:
    """Default features: a basket of cheap technical signals.

    Used when the user doesn't pass a custom feature builder. Mirrors
    what shared.alpha.base helpers compute, so the model gets the same
    raw inputs as the rule-based alphas — but with learned weights.
    """
    from shared.alpha.base import adx, atr, bollinger_pctb, ema, rolling_zscore, rsi

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    log_ret = np.log(close).diff()

    feats = pd.DataFrame(index=df.index)
    feats["ret_1"] = log_ret
    feats["ret_24"] = log_ret.rolling(24).sum()
    feats["ret_72"] = log_ret.rolling(72).sum()
    feats["ret_168"] = log_ret.rolling(168).sum()
    feats["rsi_14"] = (rsi(close, 14) - 50.0) / 50.0
    feats["bb_pctb"] = bollinger_pctb(close, 20, 2.0) - 0.5
    feats["adx_14"] = adx(high, low, close, 14) / 100.0
    feats["ema_diff_50_200"] = (ema(close, 50) - ema(close, 200)) / atr(high, low, close, 14)
    feats["atr_z"] = rolling_zscore(atr(high, low, close, 14), 168)
    feats["vol_z"] = rolling_zscore(log_ret.rolling(24).std(ddof=0), 168)
    return feats.replace([np.inf, -np.inf], np.nan).fillna(0.0)


class MetaMLAlpha(Alpha):
    """Walk-forward ridge over a feature panel.

    Params:
      refit_every: bars between refits (default 720 = 30d hourly)
      train_window: bars used per fit (default 2000 ≈ 12 weeks)
      ridge_alpha: ridge regularization strength
      target_horizon: how many bars ahead the model is asked to predict
      position_scale: divisor inside the tanh; smaller → more aggressive
    """

    DEFAULT_PARAMS = {
        "refit_every": 720,
        "train_window": 2000,
        "ridge_alpha": 5.0,
        "target_horizon": 1,
        "position_scale": 0.005,
        "min_position": 0.0,
    }

    def __init__(
        self,
        config: AlphaConfig | None = None,
        feature_builder: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> None:
        cfg = config or AlphaConfig(name="ml_meta", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)
        self._feature_builder = feature_builder or default_feature_builder

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        feats = self._feature_builder(df)
        if feats.empty or feats.shape[1] == 0:
            return pd.Series(0.0, index=df.index)

        close = df["close"].astype(float)
        log_ret = np.log(close).diff()
        target = log_ret.shift(-int(p["target_horizon"])).fillna(0.0)

        position = pd.Series(0.0, index=df.index)
        train_win = int(p["train_window"])
        refit_every = int(p["refit_every"])

        last_fit: _RidgeFit | None = None
        # Walk forward in `refit_every` chunks
        for start in range(train_win, len(df), refit_every):
            train_X = feats.iloc[start - train_win : start].values
            train_y = target.iloc[start - train_win : start].values
            if np.any(np.isnan(train_X)) or np.any(np.isnan(train_y)):
                # Drop bad rows in this window
                mask = ~(np.any(np.isnan(train_X), axis=1) | np.isnan(train_y))
                train_X = train_X[mask]
                train_y = train_y[mask]
            if len(train_X) < 50:
                continue
            last_fit = _ridge_fit(train_X, train_y, alpha=float(p["ridge_alpha"]))

            # Apply this fit forward until the next refit
            end = min(start + refit_every, len(df))
            test_X = feats.iloc[start:end].values
            preds = _ridge_predict(last_fit, test_X)
            scaled = np.tanh(preds / max(p["position_scale"], 1e-9))
            min_p = float(p["min_position"])
            if min_p > 0:
                # Floor active positions
                sign = np.sign(scaled)
                mag = np.abs(scaled)
                mag = np.where(mag > 0, np.maximum(mag, min_p), 0.0)
                scaled = sign * mag
            position.iloc[start:end] = scaled

        return position
