"""Portfolio optimization using PyPortfolioOpt.

Production-grade implementation with:
- Historical return estimation from market-data service candles
- Ledoit-Wolf shrinkage covariance estimation
- CAPM-style momentum expected returns (exponentially decayed)
- Multiple methods: max_sharpe, min_volatility, risk_parity
- Inverse-volatility fallback (not naive equal-weight)
- Proper edge-case handling
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("portfolio-service")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MARKET_DATA_BASE = "http://market-data:8001"
HISTORY_LIMIT = 720  # 30 days of 1h candles
DECAY_HALFLIFE_DAYS = 15  # exponential decay half-life for momentum returns
ANNUALIZATION_FACTOR = 365 * 24  # hourly candles -> annual
MIN_OBSERVATIONS = 48  # minimum 2 days of hourly data
RISK_FREE_RATE_DEFAULT = 0.05


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_candle_history(asset: str, limit: int = HISTORY_LIMIT) -> pd.DataFrame | None:
    """Fetch OHLCV history from the market-data service.

    Returns a DataFrame indexed by timestamp with at least a 'close' column,
    or None if the fetch fails.
    """
    import httpx

    url = f"{MARKET_DATA_BASE}/candles/{asset}/history"
    try:
        resp = httpx.get(url, params={"limit": limit}, timeout=5.0)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        return df
    except Exception as exc:
        logger.warning(
            "candle_fetch_failed",
            extra={"asset": asset, "error": str(exc)},
        )
        return None


def _build_price_matrix(assets: list[str]) -> pd.DataFrame | None:
    """Build a close-price matrix (columns = assets) from market-data candles.

    Returns None if insufficient data is available.
    """
    frames: dict[str, pd.Series] = {}
    for asset in assets:
        df = _fetch_candle_history(asset)
        if df is not None and "close" in df.columns and len(df) >= MIN_OBSERVATIONS:
            frames[asset] = df["close"]

    if len(frames) < 2:
        return None

    prices = pd.DataFrame(frames)
    # Forward-fill small gaps (up to 3h), then drop remaining NaN rows
    prices = prices.ffill(limit=3).dropna()

    if len(prices) < MIN_OBSERVATIONS:
        return None

    return prices


# ---------------------------------------------------------------------------
# Return & covariance estimation
# ---------------------------------------------------------------------------

def _estimate_expected_returns(
    prices: pd.DataFrame,
    halflife_days: float = DECAY_HALFLIFE_DAYS,
) -> pd.Series:
    """Exponentially-weighted momentum returns (annualized).

    Uses recent returns with exponential decay to capture short-term momentum
    while down-weighting stale observations. More robust than simple mean
    return for crypto assets.
    """
    log_returns = np.log(prices / prices.shift(1)).dropna()

    halflife_periods = halflife_days * 24  # convert days to hourly periods
    ewm_mean = log_returns.ewm(halflife=halflife_periods).mean().iloc[-1]

    # Annualize: hourly log return -> annual
    annual_returns = ewm_mean * ANNUALIZATION_FACTOR

    return annual_returns


def _estimate_covariance(prices: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf shrinkage covariance matrix (annualized).

    Shrinks the sample covariance toward a structured target to reduce
    estimation error, especially when n_observations / n_assets is small.
    """
    from pypfopt import risk_models

    # risk_models.CovarianceShrinkage uses Ledoit-Wolf by default
    cov = risk_models.CovarianceShrinkage(prices, frequency=ANNUALIZATION_FACTOR).ledoit_wolf()
    return cov


# ---------------------------------------------------------------------------
# Fallback strategies
# ---------------------------------------------------------------------------

def _inverse_volatility_weights(
    prices: pd.DataFrame | None,
    assets: list[str],
) -> dict[str, float]:
    """Inverse-volatility weighting — a meaningful fallback.

    Assets with lower volatility get proportionally higher weight.
    If no price data is available, falls back to equal weight.
    """
    if prices is not None and len(prices) >= MIN_OBSERVATIONS:
        log_returns = np.log(prices / prices.shift(1)).dropna()
        vols = log_returns.std()
        # Guard against zero-vol assets
        vols = vols.replace(0, vols[vols > 0].min() if (vols > 0).any() else 1.0)
        inv_vol = 1.0 / vols
        weights = inv_vol / inv_vol.sum()
        return {a: round(float(w), 6) for a, w in weights.items()}

    # True last resort: equal weight
    n = len(assets)
    return {a: round(1.0 / n, 6) for a in assets}


def _compute_portfolio_metrics(
    weights: dict[str, float],
    mu: pd.Series,
    cov: pd.DataFrame,
    risk_free_rate: float,
) -> dict[str, float]:
    """Compute expected return, volatility, Sharpe from weights + estimates."""
    w = np.array([weights.get(a, 0.0) for a in mu.index])
    port_return = float(w @ mu.values)
    port_vol = float(np.sqrt(w @ cov.values @ w))
    sharpe = (port_return - risk_free_rate) / port_vol if port_vol > 1e-9 else 0.0
    return {
        "expected_return": round(port_return, 6),
        "volatility": round(port_vol, 6),
        "sharpe_ratio": round(sharpe, 4),
    }


# ---------------------------------------------------------------------------
# Risk-parity implementation
# ---------------------------------------------------------------------------

def _risk_parity_weights(cov: pd.DataFrame) -> dict[str, float]:
    """Equal risk contribution (risk-parity) via iterative optimization.

    Each asset contributes the same marginal risk to the portfolio.
    Uses simple iterative method from Maillard, Roncalli & Teiletche (2010).
    """
    n = cov.shape[0]
    assets = cov.columns.tolist()
    sigma = cov.values

    # Start from inverse-vol
    diag_vol = np.sqrt(np.diag(sigma))
    diag_vol[diag_vol == 0] = 1.0
    w = (1.0 / diag_vol)
    w = w / w.sum()

    for _ in range(500):
        port_vol = np.sqrt(w @ sigma @ w)
        if port_vol < 1e-12:
            break
        marginal_risk = sigma @ w
        risk_contrib = w * marginal_risk / port_vol
        target = port_vol / n
        # Newton-like update
        w_new = w * (target / (risk_contrib + 1e-12))
        w_new = w_new / w_new.sum()
        if np.max(np.abs(w_new - w)) < 1e-8:
            w = w_new
            break
        w = w_new

    w = np.maximum(w, 0.0)
    w = w / w.sum()
    return {a: round(float(w[i]), 6) for i, a in enumerate(assets)}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def optimize_weights(
    positions: dict[str, float],
    price_data: pd.DataFrame | None = None,
    expected_returns: dict[str, float] | None = None,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
    method: str = "max_sharpe",
) -> dict[str, Any]:
    """Optimize portfolio weights given current positions.

    Args:
        positions: {asset: current_weight} e.g. {"BTCUSDT": 0.6, "ETHUSDT": 0.3}
        price_data: Pre-built close-price DataFrame (columns=assets). If None,
                    fetches from market-data service.
        expected_returns: Override for expected annual returns per asset.
                         If None, computed from price history.
        risk_free_rate: Annual risk-free rate for Sharpe calculation.
        method: "max_sharpe" | "min_volatility" | "risk_parity"

    Returns:
        Dict with optimized_weights, method, metrics, and diagnostics.
    """
    assets = list(positions.keys())

    # --- Single asset: trivially 100% ---
    if len(assets) < 2:
        asset = assets[0] if assets else "UNKNOWN"
        return {
            "optimized_weights": {asset: 1.0},
            "method": "single_asset",
            "metrics": {},
        }

    # --- Acquire price data ---
    prices = price_data
    if prices is None:
        prices = _build_price_matrix(assets)

    data_quality = "full"
    available_assets = list(prices.columns) if prices is not None else []

    # Handle partial data: some assets have history, some don't
    if prices is not None and set(available_assets) != set(assets):
        missing = set(assets) - set(available_assets)
        if len(available_assets) < 2:
            prices = None
            data_quality = "insufficient"
        else:
            data_quality = "partial"
            logger.info(
                "partial_price_data",
                extra={"missing": list(missing), "available": available_assets},
            )

    # --- No usable price data: inverse-volatility fallback ---
    if prices is None:
        logger.warning("no_price_data_available", extra={"assets": assets})
        fallback_weights = _inverse_volatility_weights(None, assets)
        return {
            "optimized_weights": fallback_weights,
            "method": "equal_weight_fallback",
            "metrics": {},
            "data_quality": "none",
            "note": "No historical data available; using equal-weight fallback.",
        }

    # --- Estimate returns & covariance ---
    try:
        if expected_returns is not None:
            mu = pd.Series(expected_returns).reindex(prices.columns)
            # Fill missing with momentum estimate
            if mu.isna().any():
                momentum = _estimate_expected_returns(prices)
                mu = mu.fillna(momentum)
        else:
            mu = _estimate_expected_returns(prices)

        cov = _estimate_covariance(prices)
    except Exception as exc:
        logger.warning(
            "estimation_failed",
            extra={"error": str(exc), "assets": available_assets},
        )
        fallback_weights = _inverse_volatility_weights(prices, available_assets)
        return {
            "optimized_weights": fallback_weights,
            "method": "inverse_volatility_fallback",
            "metrics": {},
            "data_quality": data_quality,
            "error": str(exc),
        }

    # --- Handle edge case: all negative expected returns ---
    if (mu <= 0).all() and method == "max_sharpe":
        logger.info("all_negative_returns_switching_to_min_vol")
        method = "min_volatility"

    # --- Optimize ---
    try:
        from pypfopt import EfficientFrontier

        if method == "risk_parity":
            weights = _risk_parity_weights(cov)
            metrics = _compute_portfolio_metrics(weights, mu, cov, risk_free_rate)
        elif method in ("max_sharpe", "min_volatility"):
            ef = EfficientFrontier(mu, cov, weight_bounds=(0, 1))

            if method == "max_sharpe":
                ef.max_sharpe(risk_free_rate=risk_free_rate)
            else:
                ef.min_volatility()

            raw_weights = ef.clean_weights(cutoff=0.01)
            weights = {a: float(w) for a, w in raw_weights.items()}
            perf = ef.portfolio_performance(risk_free_rate=risk_free_rate)
            metrics = {
                "expected_return": round(perf[0], 6),
                "volatility": round(perf[1], 6),
                "sharpe_ratio": round(perf[2], 4),
            }
        else:
            raise ValueError(f"Unknown optimization method: {method}")

        # Handle degenerate result (all weights zero after cleaning)
        if sum(weights.values()) < 0.01:
            raise ValueError("Optimizer produced near-zero total weight")

        # Add partial-data assets back with zero weight
        for a in assets:
            if a not in weights:
                weights[a] = 0.0

        return {
            "optimized_weights": weights,
            "method": method,
            "metrics": metrics,
            "data_quality": data_quality,
            "n_observations": len(prices),
        }

    except Exception as exc:
        logger.warning(
            "optimization_failed_using_inverse_vol",
            extra={"method": method, "error": str(exc)},
        )
        fallback_weights = _inverse_volatility_weights(prices, available_assets)
        # Compute metrics for the fallback too
        try:
            metrics = _compute_portfolio_metrics(fallback_weights, mu, cov, risk_free_rate)
        except Exception:
            metrics = {}

        # Add missing assets with zero weight
        for a in assets:
            if a not in fallback_weights:
                fallback_weights[a] = 0.0

        return {
            "optimized_weights": fallback_weights,
            "method": "inverse_volatility_fallback",
            "metrics": metrics,
            "data_quality": data_quality,
            "error": str(exc),
        }
