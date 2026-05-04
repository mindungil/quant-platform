"""Tests for Brinson-Fachler attribution in risk_model.py.

Verifies the fix for the selection/interaction bug where both effects
were always zero due to using the same return dict for portfolio and
benchmark asset returns.
"""
from __future__ import annotations

import numpy as np
import pytest

from shared.portfolio.risk_model import brinson_attribution, fit_pca_factor_model
import pandas as pd


class TestBrinsonAttribution:
    """Brinson-Fachler allocation + selection + interaction."""

    def test_same_returns_collapses_selection_to_zero(self):
        """When benchmark_asset_returns is None (same assets), selection=0."""
        result = brinson_attribution(
            portfolio_weights={"BTC": 0.6, "ETH": 0.4},
            benchmark_weights={"BTC": 0.5, "ETH": 0.5},
            asset_returns={"BTC": 0.05, "ETH": -0.02},
            benchmark_return=0.015,
        )
        assert result["selection_effect"] == 0.0
        assert result["interaction_effect"] == 0.0
        # Allocation should be non-zero because weights differ
        assert result["allocation_effect"] != 0.0

    def test_different_returns_produces_selection(self):
        """When portfolio and benchmark asset returns differ, selection != 0."""
        result = brinson_attribution(
            portfolio_weights={"BTC": 0.6, "ETH": 0.4},
            benchmark_weights={"BTC": 0.5, "ETH": 0.5},
            asset_returns={"BTC": 0.08, "ETH": -0.01},
            benchmark_return=0.015,
            benchmark_asset_returns={"BTC": 0.05, "ETH": -0.02},
        )
        # Selection = 0.5*(0.08-0.05) + 0.5*(-0.01-(-0.02)) = 0.015 + 0.005 = 0.02
        assert abs(result["selection_effect"] - 0.02) < 1e-9
        # Interaction = (0.6-0.5)*(0.08-0.05) + (0.4-0.5)*(-0.01-(-0.02))
        #             = 0.1*0.03 + (-0.1)*0.01 = 0.003 - 0.001 = 0.002
        assert abs(result["interaction_effect"] - 0.002) < 1e-9

    def test_allocation_effect_math(self):
        """Allocation = Σ (w_p - w_b) * (r_b,i - r_b_total)."""
        result = brinson_attribution(
            portfolio_weights={"A": 0.7, "B": 0.3},
            benchmark_weights={"A": 0.5, "B": 0.5},
            asset_returns={"A": 0.10, "B": 0.02},
            benchmark_return=0.06,
            benchmark_asset_returns={"A": 0.10, "B": 0.02},
        )
        # Allocation = (0.7-0.5)*(0.10-0.06) + (0.3-0.5)*(0.02-0.06)
        #            = 0.2*0.04 + (-0.2)*(-0.04) = 0.008 + 0.008 = 0.016
        assert abs(result["allocation_effect"] - 0.016) < 1e-9

    def test_active_return_equals_sum_of_effects_when_returns_differ(self):
        """active_return ≈ allocation + selection + interaction."""
        result = brinson_attribution(
            portfolio_weights={"X": 0.8, "Y": 0.2},
            benchmark_weights={"X": 0.4, "Y": 0.6},
            asset_returns={"X": 0.06, "Y": -0.03},
            benchmark_return=0.01,
            benchmark_asset_returns={"X": 0.04, "Y": -0.01},
        )
        decomposed = (
            result["allocation_effect"]
            + result["selection_effect"]
            + result["interaction_effect"]
        )
        assert abs(result["active_return"] - decomposed) < 1e-6

    def test_empty_portfolio(self):
        """Zero-weight portfolio returns zero effects."""
        result = brinson_attribution(
            portfolio_weights={},
            benchmark_weights={"A": 1.0},
            asset_returns={"A": 0.05},
            benchmark_return=0.05,
        )
        assert result["portfolio_return"] == 0.0
        assert result["active_return"] == -0.05

    def test_missing_assets_default_to_zero(self):
        """Assets in one dict but not others get 0 weight/return."""
        result = brinson_attribution(
            portfolio_weights={"A": 1.0},
            benchmark_weights={"B": 1.0},
            asset_returns={"A": 0.05, "B": 0.03},
            benchmark_return=0.03,
            benchmark_asset_returns={"A": 0.04, "B": 0.03},
        )
        # Should not raise; missing keys default to 0.0
        assert "portfolio_return" in result

    def test_backward_compatible_without_benchmark_returns(self):
        """Calling without benchmark_asset_returns still works (legacy)."""
        result = brinson_attribution(
            portfolio_weights={"A": 0.5, "B": 0.5},
            benchmark_weights={"A": 0.5, "B": 0.5},
            asset_returns={"A": 0.10, "B": 0.02},
            benchmark_return=0.06,
        )
        # Same weights → allocation = 0, same returns → selection = 0
        assert result["allocation_effect"] == 0.0
        assert result["selection_effect"] == 0.0
        assert result["interaction_effect"] == 0.0


class TestPCAFactorModel:
    """Smoke tests for PCA factor model."""

    def test_fit_and_decompose(self):
        rng = np.random.default_rng(42)
        T, N = 200, 4
        returns = pd.DataFrame(
            rng.normal(0, 0.01, (T, N)),
            columns=["BTC", "ETH", "SOL", "BNB"],
        )
        model = fit_pca_factor_model(returns, n_factors=2)
        assert model.factor_loadings.shape == (N, 2)
        assert model.factor_returns.shape == (T, 2)

        w = np.array([0.3, 0.3, 0.2, 0.2])
        rd = model.risk_decomposition(w)
        assert rd["total_variance"] > 0
        assert 0 <= rd["factor_share"] <= 1

    def test_attribute_pnl(self):
        rng = np.random.default_rng(42)
        T, N = 200, 3
        returns = pd.DataFrame(rng.normal(0, 0.01, (T, N)), columns=["A", "B", "C"])
        model = fit_pca_factor_model(returns, n_factors=2)
        w = np.array([0.5, 0.3, 0.2])
        attr = model.attribute_pnl(w)
        # factor + specific ≈ total
        assert abs(attr["factor"]["sum"] + attr["specific"]["sum"] - attr["total"]["sum"]) < 1e-10
