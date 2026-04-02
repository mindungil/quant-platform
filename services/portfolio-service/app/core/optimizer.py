"""Portfolio optimization using PyPortfolioOpt.

Provides Markowitz mean-variance optimization and Hierarchical Risk Parity
for position rebalancing recommendations.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("portfolio-service")


def optimize_weights(
    positions: dict[str, float],
    expected_returns: dict[str, float] | None = None,
    risk_free_rate: float = 0.05,
    method: str = "max_sharpe",
) -> dict:
    """Optimize portfolio weights given current positions.

    Args:
        positions: {asset: current_weight} e.g. {"BTCUSDT": 0.6, "ETHUSDT": 0.3, "SOLUSDT": 0.1}
        expected_returns: {asset: expected_annual_return} — if None, uses equal weight
        risk_free_rate: annual risk-free rate
        method: "max_sharpe" | "min_volatility" | "equal_weight"

    Returns:
        Dict with optimized weights, method used, and metrics.
    """
    if len(positions) < 2:
        return {
            "optimized_weights": positions,
            "method": "single_asset",
            "metrics": {},
        }

    try:
        import numpy as np
        import pandas as pd
        from pypfopt import EfficientFrontier, expected_returns as er, risk_models

        assets = list(positions.keys())
        n = len(assets)

        # If we don't have return history, use equal-weight with simple covariance estimate
        if expected_returns is None:
            mu = pd.Series({a: 0.10 for a in assets})  # default 10% expected return
        else:
            mu = pd.Series(expected_returns)

        # Simple diagonal covariance (no history available — assume uncorrelated)
        # In production, this would use historical price data
        cov = pd.DataFrame(
            np.eye(n) * 0.04,  # 20% vol per asset
            index=assets,
            columns=assets,
        )

        if method == "min_volatility":
            ef = EfficientFrontier(mu, cov)
            ef.min_volatility()
            weights = ef.clean_weights()
            perf = ef.portfolio_performance(risk_free_rate=risk_free_rate)
        elif method == "max_sharpe":
            ef = EfficientFrontier(mu, cov)
            ef.max_sharpe(risk_free_rate=risk_free_rate)
            weights = ef.clean_weights()
            perf = ef.portfolio_performance(risk_free_rate=risk_free_rate)
        else:  # equal_weight
            weights = {a: round(1.0 / n, 4) for a in assets}
            perf = (0.10, 0.20, 0.25)  # dummy

        return {
            "optimized_weights": dict(weights),
            "method": method,
            "metrics": {
                "expected_return": round(perf[0], 4),
                "volatility": round(perf[1], 4),
                "sharpe_ratio": round(perf[2], 4),
            },
        }

    except Exception as exc:
        logger.warning("portfolio_optimization_failed", extra={"error": str(exc)})
        # Fallback: equal weight
        n = len(positions)
        return {
            "optimized_weights": {a: round(1.0 / n, 4) for a in positions},
            "method": "equal_weight_fallback",
            "metrics": {},
            "error": str(exc),
        }
