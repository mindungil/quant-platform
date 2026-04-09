"""Market regime detection.

VolTrendRegime — fast, no-training 4-state classifier:
  0: TREND_UP    (positive trend z, normal vol)
  1: TREND_DOWN  (negative trend z, normal vol)
  2: RANGE       (|trend z| small, normal vol)
  3: CRISIS      (vol z >> 0, irrespective of trend)

HMMRegime — K-state Gaussian HMM trained on log-returns. Uses a clean
Baum-Welch implementation in numpy. Reasonable for K=2..4.

Both classes produce:
  - .label    (Series of int regime IDs)
  - .proba    (DataFrame of [n_bars × K] state probabilities, columns
               are state IDs)

Use proba (not label) when feeding the ensemble — soft assignments are
robust to regime-switch latency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class RegimeOutput:
    label: pd.Series
    proba: pd.DataFrame
    state_names: list[str]


# ---- VolTrendRegime ----


@dataclass
class VolTrendRegime:
    vol_window: int = 168            # bars for rolling vol
    trend_window: int = 168          # bars for rolling trend (z-score of return mean)
    crisis_vol_z: float = 2.0        # vol z-score above this → CRISIS
    range_trend_z_max: float = 0.5   # |trend z| below this → RANGE
    z_smooth: int = 12               # smoothing on z-scores

    STATE_NAMES = ["TREND_UP", "TREND_DOWN", "RANGE", "CRISIS"]

    def fit_predict(self, df: pd.DataFrame) -> RegimeOutput:
        if "close" not in df.columns:
            raise ValueError("df must have 'close' column")
        close = df["close"].astype(float)
        log_ret = np.log(close).diff()

        vol = log_ret.rolling(self.vol_window, min_periods=20).std(ddof=0)
        trend = log_ret.rolling(self.trend_window, min_periods=20).mean()

        # Long-term baselines for z-scoring
        vol_baseline = vol.rolling(self.vol_window * 4, min_periods=50).mean()
        vol_baseline_std = vol.rolling(self.vol_window * 4, min_periods=50).std(ddof=0)
        trend_baseline = trend.rolling(self.trend_window * 4, min_periods=50).mean()
        trend_baseline_std = trend.rolling(self.trend_window * 4, min_periods=50).std(ddof=0)

        vol_z = ((vol - vol_baseline) / vol_baseline_std.replace(0, np.nan)).fillna(0.0)
        trend_z = ((trend - trend_baseline) / trend_baseline_std.replace(0, np.nan)).fillna(0.0)

        if self.z_smooth > 1:
            vol_z = vol_z.ewm(span=self.z_smooth, adjust=False).mean()
            trend_z = trend_z.ewm(span=self.z_smooth, adjust=False).mean()

        labels = pd.Series(2, index=close.index, dtype=int)  # default RANGE
        labels = labels.mask(trend_z > self.range_trend_z_max, 0)        # TREND_UP
        labels = labels.mask(trend_z < -self.range_trend_z_max, 1)       # TREND_DOWN
        labels = labels.mask(vol_z > self.crisis_vol_z, 3)                # CRISIS overrides

        # Soft probabilities via softmax over distance to each prototype
        n = len(close)
        proba = np.zeros((n, 4))
        # State centers in (trend_z, vol_z) space
        centers = np.array([
            [+1.0, 0.0],   # TREND_UP
            [-1.0, 0.0],   # TREND_DOWN
            [ 0.0, 0.0],   # RANGE
            [ 0.0, 2.5],   # CRISIS
        ])
        feats = np.column_stack([trend_z.values, vol_z.values])
        for i, c in enumerate(centers):
            d = ((feats - c) ** 2).sum(axis=1)
            proba[:, i] = np.exp(-0.5 * d)
        row_sum = proba.sum(axis=1, keepdims=True)
        row_sum[row_sum == 0] = 1.0
        proba = proba / row_sum

        proba_df = pd.DataFrame(proba, index=close.index, columns=self.STATE_NAMES)
        return RegimeOutput(label=labels, proba=proba_df, state_names=list(self.STATE_NAMES))


# ---- HMMRegime ----


@dataclass
class HMMRegime:
    n_states: int = 3
    max_iter: int = 30
    tol: float = 1e-4
    seed: int = 0

    def fit_predict(self, df: pd.DataFrame) -> RegimeOutput:
        if "close" not in df.columns:
            raise ValueError("df must have 'close' column")
        close = df["close"].astype(float)
        log_ret = np.log(close).diff().dropna()
        obs = log_ret.values.astype(float)
        n = len(obs)
        K = self.n_states

        rng = np.random.default_rng(self.seed)

        # ---- init: K-quantile means, equal priors, near-uniform transitions ----
        sorted_obs = np.sort(obs)
        means = np.array([
            sorted_obs[int((i + 0.5) / K * n)]
            for i in range(K)
        ])
        sigmas = np.full(K, max(obs.std(), 1e-4))
        pi = np.full(K, 1.0 / K)
        A = np.full((K, K), (1.0 - 0.9) / (K - 1)) if K > 1 else np.array([[1.0]])
        if K > 1:
            np.fill_diagonal(A, 0.9)

        prev_ll = -np.inf
        for it in range(self.max_iter):
            # ---- E-step ----
            B = self._emission_prob(obs, means, sigmas)  # n × K
            alpha, c = self._forward(pi, A, B)
            beta = self._backward(A, B, c)
            gamma = alpha * beta
            gamma_sum = gamma.sum(axis=1, keepdims=True)
            gamma_sum[gamma_sum == 0] = 1.0
            gamma = gamma / gamma_sum

            xi_sum = np.zeros((K, K))
            for t in range(n - 1):
                denom = float((alpha[t][:, None] * A * B[t + 1][None, :] * beta[t + 1][None, :]).sum())
                if denom > 0:
                    xi_sum += (alpha[t][:, None] * A * B[t + 1][None, :] * beta[t + 1][None, :]) / denom

            # ---- M-step ----
            pi = gamma[0] / gamma[0].sum()
            row_sums = gamma[:-1].sum(axis=0)
            row_sums[row_sums == 0] = 1.0
            A = xi_sum / row_sums[:, None]
            # Renormalize rows
            row_sums_A = A.sum(axis=1, keepdims=True)
            row_sums_A[row_sums_A == 0] = 1.0
            A = A / row_sums_A

            gam_sum = gamma.sum(axis=0)
            gam_sum[gam_sum == 0] = 1.0
            means = (gamma * obs[:, None]).sum(axis=0) / gam_sum
            var = ((gamma * (obs[:, None] - means[None, :]) ** 2).sum(axis=0)) / gam_sum
            sigmas = np.sqrt(np.maximum(var, 1e-10))

            ll = -np.sum(np.log(c + 1e-12))
            if abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll

        # ---- final probabilities ----
        B = self._emission_prob(obs, means, sigmas)
        alpha, c = self._forward(pi, A, B)
        beta = self._backward(A, B, c)
        gamma = alpha * beta
        gsum = gamma.sum(axis=1, keepdims=True)
        gsum[gsum == 0] = 1.0
        gamma = gamma / gsum

        labels = np.argmax(gamma, axis=1)

        # Re-align to original index (we dropped the first NaN return)
        idx = close.index[1:]
        # Order states by mean for stability (state 0 = lowest mean)
        order = np.argsort(means)
        relabel = {old: new for new, old in enumerate(order)}
        labels = np.array([relabel[l] for l in labels])
        gamma = gamma[:, order]

        labels_full = pd.Series(0, index=close.index, dtype=int)
        labels_full.iloc[1:] = labels
        proba_full = pd.DataFrame(
            np.zeros((len(close), K)),
            index=close.index,
            columns=[f"S{i}" for i in range(K)],
        )
        proba_full.iloc[1:] = gamma

        return RegimeOutput(
            label=labels_full,
            proba=proba_full,
            state_names=[f"S{i}_mu={means[order[i]]:.4f}" for i in range(K)],
        )

    @staticmethod
    def _emission_prob(obs: np.ndarray, means: np.ndarray, sigmas: np.ndarray) -> np.ndarray:
        # Gaussian PDF
        diff = obs[:, None] - means[None, :]
        var = sigmas[None, :] ** 2
        return np.exp(-0.5 * diff * diff / var) / np.sqrt(2 * np.pi * var)

    @staticmethod
    def _forward(pi: np.ndarray, A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n, K = B.shape
        alpha = np.zeros((n, K))
        c = np.zeros(n)
        alpha[0] = pi * B[0]
        c[0] = alpha[0].sum() or 1.0
        alpha[0] /= c[0]
        for t in range(1, n):
            alpha[t] = (alpha[t - 1] @ A) * B[t]
            c[t] = alpha[t].sum() or 1.0
            alpha[t] /= c[t]
        return alpha, c

    @staticmethod
    def _backward(A: np.ndarray, B: np.ndarray, c: np.ndarray) -> np.ndarray:
        n, K = B.shape
        beta = np.zeros((n, K))
        beta[-1] = 1.0 / (c[-1] or 1.0)
        for t in range(n - 2, -1, -1):
            beta[t] = (A @ (B[t + 1] * beta[t + 1])) / (c[t] or 1.0)
        return beta
