"""Tests for NCO and correlation denoising."""
from __future__ import annotations

import numpy as np

from shared.portfolio.nco import (
    NCOConfig,
    cov_to_corr,
    denoise_corr,
    marchenko_pastur_max,
    nco_weights,
)


def test_marchenko_pastur_max_monotone():
    a = marchenko_pastur_max(100, 5)
    b = marchenko_pastur_max(100, 50)
    assert b > a  # more features → higher noise bound


def test_denoise_corr_preserves_diag():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((400, 8))
    cov = np.cov(X, rowvar=False)
    corr, _ = cov_to_corr(cov)
    denoised = denoise_corr(corr, n_obs=400)
    assert np.allclose(np.diag(denoised), 1.0)
    assert denoised.shape == corr.shape


def test_nco_weights_sum_to_one():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((300, 6))
    cov = np.cov(X, rowvar=False)
    w = nco_weights(cov, NCOConfig(max_clusters=3))
    assert abs(w.sum() - 1.0) < 1e-9
    assert (w >= 0).all()


def test_nco_concentrates_on_low_variance_assets():
    # Build a covariance with one extremely volatile asset
    n = 5
    cov = np.eye(n)
    cov[0, 0] = 100.0  # very volatile
    w = nco_weights(cov, NCOConfig(max_clusters=2))
    # The volatile asset should get the smallest weight
    assert w[0] < w[1:].max()


def test_nco_handles_singleton():
    cov = np.array([[0.04]])
    w = nco_weights(cov)
    assert w[0] == 1.0
