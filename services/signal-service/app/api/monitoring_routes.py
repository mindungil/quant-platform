"""Monitoring API endpoints for the /monitoring frontend page.

Exposes real alpha health, feature importance, mining history, and
system metrics. Falls back to empty payloads when underlying data
is unavailable (the frontend should show empty states, not errors).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("monitoring-routes")

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

STRATEGY_REGISTRY_URL = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
PORTFOLIO_SERVICE_URL = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
MARKET_DATA_URL = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")


def _sharpe(returns: pd.Series, periods_per_year: int = 24 * 365) -> float:
    """Compute annualized Sharpe ratio from bar returns."""
    if len(returns) < 10 or returns.std() < 1e-12:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _max_drawdown(returns: pd.Series) -> float:
    """Compute fractional max drawdown from bar returns."""
    if len(returns) == 0:
        return 0.0
    equity = np.cumprod(1.0 + returns.values)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
    return float(abs(dd.min())) if len(dd) > 0 else 0.0


@router.get("/alphas/health")
async def alpha_health(asset: str = Query("BTCUSDT")) -> dict[str, Any]:
    """Per-alpha health status with multi-horizon Sharpe.

    Runs each production alpha on recent OHLCV and computes Sharpe
    over 7d (168 bars), 30d (720 bars), 90d (2160 bars) windows.
    Status derived from Sharpe degradation: HEALTHY if 7d > 0,
    DEGRADED if 7d < 0 but 30d > 0, CRITICAL if both < 0.
    """
    try:
        from shared.alpha.registry import (
            PRODUCTION_READY_ALPHAS,
            get_alpha,
        )
        import httpx

        # Fetch recent candles
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{MARKET_DATA_URL}/candles/{asset}/history",
                params={"interval": "1h", "limit": 2400},
            )
            if resp.status_code != 200:
                return {"asset": asset, "alphas": [], "error": "market_data_unavailable"}
            candles = resp.json()
            if not candles:
                return {"asset": asset, "alphas": [], "error": "no_candles"}

        df = pd.DataFrame(candles)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        df = df.astype({c: float for c in ["open", "high", "low", "close", "volume"] if c in df.columns})

        bar_ret = df["close"].pct_change().fillna(0.0)

        results = []
        for name in sorted(PRODUCTION_READY_ALPHAS):
            try:
                alpha = get_alpha(name)
                sig = alpha.generate(df)
                pnl = (sig.position * bar_ret).fillna(0.0)

                sharpe_7d = _sharpe(pnl.iloc[-168:]) if len(pnl) >= 168 else 0.0
                sharpe_30d = _sharpe(pnl.iloc[-720:]) if len(pnl) >= 720 else 0.0
                sharpe_90d = _sharpe(pnl.iloc[-2160:]) if len(pnl) >= 2160 else 0.0

                if sharpe_7d > 0.1:
                    status = "HEALTHY"
                    weight = 1.0
                elif sharpe_7d > -0.1 and sharpe_30d > 0:
                    status = "DEGRADED"
                    weight = 0.5
                else:
                    status = "CRITICAL"
                    weight = 0.0

                results.append({
                    "name": name,
                    "status": status,
                    "sharpe_7d": round(sharpe_7d, 3),
                    "sharpe_30d": round(sharpe_30d, 3),
                    "sharpe_90d": round(sharpe_90d, 3),
                    "weight": round(weight, 2),
                })
            except Exception as exc:
                logger.warning("alpha_health_failed", extra={"alpha": name, "error": str(exc)[:100]})
                continue

        return {
            "asset": asset,
            "alphas": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bars_used": len(df),
        }
    except Exception as exc:
        logger.exception("alpha_health_error")
        raise HTTPException(status_code=500, detail=str(exc)[:200])


@router.get("/features/importance")
async def feature_importance(
    asset: str = Query("BTCUSDT"),
    top_n: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """Top-N features by IC Information Ratio.

    Uses shared.features.importance to compute rolling IC of each
    feature against 1-bar-ahead returns. Returns top features by
    IC_IR (signal stability).
    """
    try:
        from shared.features.engine import FeatureEngine
        from shared.features.importance import compute_rolling_ic, rank_features
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{MARKET_DATA_URL}/candles/{asset}/history",
                params={"limit": 1500},
            )
            if resp.status_code != 200:
                return {"asset": asset, "features": [], "error": "market_data_unavailable"}
            candles = resp.json()

        df = pd.DataFrame(candles)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        df = df.astype({c: float for c in ["open", "high", "low", "close", "volume"] if c in df.columns})

        engine = FeatureEngine()
        fm = engine.generate(df)
        fwd = df["close"].pct_change().shift(-1).fillna(0.0)

        ic_panel = compute_rolling_ic(fm.features, fwd, window=200)
        report = rank_features(ic_panel, top_n=top_n, min_ic_ir=0.1)

        # Categorize features
        category_map = {m.name: m.category for m in fm.metadata}

        features = []
        for i, name in enumerate(report.top_features[:top_n]):
            features.append({
                "name": name,
                "importance": round(abs(report.ic_ir.get(name, 0)), 4),
                "ic_mean": round(report.ic_mean.get(name, 0), 4),
                "category": category_map.get(name, "unknown"),
                "rank": i + 1,
            })

        return {
            "asset": asset,
            "features": features,
            "total_features": report.n_features_total,
            "stable_features": report.n_features_stable,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.exception("feature_importance_error")
        raise HTTPException(status_code=500, detail=str(exc)[:200])


@router.get("/mining/history")
async def mining_history(limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    """Alpha mining runs history from strategy-registry.

    Each mining pass tests candidate alpha variations (parameter sweeps,
    feature additions) and records how many passed the 8-yr validation
    gates. Data pulled from strategy-registry /mining/history endpoint.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{STRATEGY_REGISTRY_URL}/mining/history",
                params={"limit": limit},
            )
            if resp.status_code == 200:
                return {
                    "runs": resp.json(),
                    "source": "strategy-registry",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
    except Exception as exc:
        logger.debug("mining_history_registry_unavailable", extra={"error": str(exc)[:100]})

    # Empty state when registry doesn't have mining history endpoint yet
    return {
        "runs": [],
        "source": "empty",
        "note": "mining history not yet available; first automated run scheduled for next month",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/metrics")
async def system_metrics() -> dict[str, Any]:
    """Aggregate system-level metrics for monitoring dashboard.

    Returns counts of active alphas/features/symbols + recent portfolio
    PnL pulled from portfolio-service.
    """
    try:
        from shared.alpha.registry import PRODUCTION_READY_ALPHAS, ALPHA_REGISTRY
        from shared.features.engine import FeatureEngine, FeatureEngineConfig

        # Count features without running full generation
        engine = FeatureEngine()
        # Use a tiny DF just to enumerate features
        tiny_df = pd.DataFrame({
            "open": [1.0] * 800, "high": [1.1] * 800, "low": [0.9] * 800,
            "close": [1.0] * 800, "volume": [100.0] * 800,
        })
        fm = engine.generate(tiny_df)
        total_features = len(fm.metadata)

        active_alphas = len(PRODUCTION_READY_ALPHAS)
        total_alphas = len(ALPHA_REGISTRY)

        # Fetch paper PnL + DD from portfolio-service
        paper_pnl = 0.0
        paper_dd = 0.0
        symbols = 0
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{PORTFOLIO_SERVICE_URL}/portfolio/system")
                if resp.status_code == 200:
                    data = resp.json()
                    paper_pnl = float(data.get("total_pnl_pct", 0)) * 100
                    paper_dd = -abs(float(data.get("total_drawdown", 0))) * 100
                    symbols = int(data.get("active_symbols", 0))
        except Exception as exc:
            logger.debug("portfolio_metrics_unavailable", extra={"error": str(exc)[:100]})

        return {
            "active_alphas": active_alphas,
            "total_alphas": total_alphas,
            "total_features": total_features,
            "symbols": symbols,
            "paper_pnl_pct": round(paper_pnl, 2),
            "paper_dd_pct": round(paper_dd, 2),
            "mining_attempts": 0,  # filled when mining endpoint ready
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.exception("system_metrics_error")
        raise HTTPException(status_code=500, detail=str(exc)[:200])
