import os
from datetime import datetime, timezone

from fastapi import APIRouter, Response
import httpx
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import compute_statistics
from app.core.config import settings
from app.core.hindsight import analyze_decision_hindsight, compute_accuracy_report
from app.db.repository import statistics_repository
from app.models.statistics import StatisticsInput, StatisticsSnapshot
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return health_payload(
        "statistics-service",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/statistics/compute", response_model=StatisticsSnapshot)
def compute(payload: StatisticsInput) -> StatisticsSnapshot:
    return compute_statistics(payload)


@router.post("/statistics/record", response_model=StatisticsSnapshot)
def record_trade(payload: StatisticsInput) -> StatisticsSnapshot:
    if payload.user_id is None:
        return compute_statistics(payload)
    pnl = payload.trade_pnls[-1] if payload.trade_pnls else 0.0
    return statistics_repository.record_trade(
        payload.user_id,
        pnl,
        payload.expected_return,
        order_id=payload.order_id,
        asset=payload.asset,
        correlation_id=payload.correlation_id,
    )


@router.post("/statistics/strategy/{strategy_id}")
def compute_strategy_stats(strategy_id: str, payload: StatisticsInput) -> StatisticsSnapshot:
    payload.strategy_id = strategy_id
    return compute_statistics(payload)


@router.get("/statistics/{user_id}", response_model=StatisticsSnapshot)
def get_statistics(user_id: str) -> StatisticsSnapshot:
    return statistics_repository.get(user_id)


@router.get("/statistics/{user_id}/equity-curve")
def equity_curve(user_id: str, strategy_id: str | None = None, window: int = 90) -> list[dict]:
    """Returns list of {date, cumulative_return, drawdown, rolling_sharpe_7d}."""
    rows = statistics_repository.get_trade_history(user_id, strategy_id=strategy_id, limit=window)
    if not rows:
        return []

    cumulative = 0.0
    peak = 0.0
    curve: list[dict] = []
    recent_returns: list[float] = []

    for row in rows:
        pnl = row["pnl"]
        cumulative += pnl
        peak = max(peak, cumulative)
        drawdown = round((peak - cumulative) / max(peak, 1e-9), 6) if peak > 0 else 0.0
        recent_returns.append(pnl)

        # Rolling 7-day Sharpe
        rolling_sharpe = 0.0
        if len(recent_returns) >= 7:
            window_returns = recent_returns[-7:]
            import numpy as np
            arr = np.array(window_returns)
            mean = float(np.mean(arr))
            std = float(np.std(arr, ddof=1))
            rolling_sharpe = round(mean / std, 4) if std > 0 else 0.0

        curve.append({
            "date": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            "cumulative_return": round(cumulative, 6),
            "drawdown": drawdown,
            "rolling_sharpe_7d": rolling_sharpe,
        })

    return curve


@router.get("/statistics/{user_id}/strategy-comparison")
def strategy_comparison(user_id: str) -> list[dict]:
    """Returns ranked strategies by Sharpe, with win_rate, trade_count, avg_return."""
    strategies = statistics_repository.get_strategy_stats(user_id)
    # Sort by Sharpe descending
    strategies.sort(key=lambda s: s.get("sharpe", 0), reverse=True)
    return strategies


@router.get("/statistics/hindsight/{asset}")
def get_hindsight_report(asset: str):
    """Get agent decision accuracy report for an asset."""
    # 1. Fetch recent decisions from crypto-agent
    agent_url = os.environ.get("CRYPTO_AGENT_BASE_URL", "http://localhost:8006")
    try:
        resp = httpx.get(f"{agent_url}/decisions/history/{asset}?limit=100", timeout=5.0)
        decisions = resp.json() if resp.status_code == 200 else []
    except Exception:
        decisions = []

    # 2. Fetch current price from market-data
    market_url = os.environ.get("MARKET_DATA_BASE_URL", "http://localhost:8001")
    try:
        resp = httpx.get(f"{market_url}/candles/{asset}/latest", timeout=5.0)
        current_price = resp.json().get("close", 0) if resp.status_code == 200 else 0
    except Exception:
        current_price = 0

    # 3. Analyze each decision
    analyses = []
    for d in decisions:
        ref_price = d.get("reference_price") or d.get("signal_score", 0)
        if ref_price and ref_price > 0 and current_price > 0:
            # Calculate hours elapsed
            try:
                dt = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                hours = 0

            d["hours_elapsed"] = round(hours, 1)
            analysis = analyze_decision_hindsight(d, current_price, ref_price)
            analyses.append(analysis)

    # 4. Compute report
    report = compute_accuracy_report(analyses)
    report["asset"] = asset
    report["current_price"] = current_price
    report["analyses"] = analyses[:20]  # latest 20 for detail view

    return report
