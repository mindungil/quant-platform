from fastapi import APIRouter, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.core.engine import run_decision_loop
from app.db.repository import decision_repository
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return health_payload(
        "crypto-agent",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/agent/mab-stats")
def get_mab_stats() -> dict:
    """Inspect live MAB state — proves whether the bandit is actually learning."""
    try:
        from app.core.mab_state import formula_mab
    except ImportError:
        raise HTTPException(status_code=503, detail="mab_unavailable_in_public_build")
    return formula_mab.get_stats()


@router.post("/agent/learning/run-fast")
async def force_fast_loop() -> dict:
    """Trigger the 5-minute hindsight loop immediately (admin/debug)."""
    try:
        from app.core.learning_scheduler import learning_scheduler
    except ImportError:
        raise HTTPException(status_code=503, detail="learning_scheduler_unavailable_in_public_build")
    await learning_scheduler._fast_loop()
    return {"ok": True, "loop": "fast"}


@router.post("/agent/learning/run-daily")
async def force_daily_loop() -> dict:
    """Trigger the daily factor-weight optimizer immediately (admin/debug)."""
    try:
        from app.core.learning_scheduler import learning_scheduler
    except ImportError:
        raise HTTPException(status_code=503, detail="learning_scheduler_unavailable_in_public_build")
    await learning_scheduler._daily_loop()
    return {"ok": True, "loop": "daily"}


@router.post("/agent/learning/run-weekly")
async def force_weekly_loop() -> dict:
    """Trigger the weekly meta-learning loop immediately (admin/debug)."""
    try:
        from app.core.learning_scheduler import learning_scheduler
    except ImportError:
        raise HTTPException(status_code=503, detail="learning_scheduler_unavailable_in_public_build")
    await learning_scheduler._weekly_loop()
    return {"ok": True, "loop": "weekly"}


@router.get("/agent/learning-status")
def get_learning_status() -> dict:
    """High-level learning health snapshot for ops/dashboard."""
    try:
        from app.core.mab_state import formula_mab
        from shared.factors.dynamic_weights import (
            get_recent_accuracy, get_active_protocol,
            load_factor_weights, load_category_weights,
        )
    except ImportError:
        raise HTTPException(status_code=503, detail="learning_status_unavailable_in_public_build")
    stats = formula_mab.get_stats()
    global_arms = stats.get("global", {})
    total_obs = sum(a.get("n", 0) for a in global_arms.values())
    learning_arms = sum(1 for a in global_arms.values() if a.get("n", 0) > 0)
    return {
        "recent_accuracy": get_recent_accuracy(),
        "active_protocol": get_active_protocol(),
        "mab": {
            "total_observations": total_obs,
            "active_arms": learning_arms,
            "total_arms": len(global_arms),
            "regimes_seen": len(stats.get("regimes", {})),
        },
        "factor_weights_persisted": len(load_factor_weights() or {}),
        "category_weights_persisted": len(load_category_weights() or {}),
    }


@router.post("/decisions/run/{asset}")
def run_decision(asset: str, x_user_id: str | None = Header(default=None)):
    return run_decision_loop(asset, user_id=x_user_id)


@router.get("/decisions/latest/{asset}")
def get_latest_decision(asset: str):
    decision = decision_repository.get_latest(asset)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return decision


@router.get("/decisions/history/{asset}")
def get_decision_history(asset: str):
    return decision_repository.get_history(asset)


@router.get("/agent/status")
def agent_status():
    from app.core.scheduler import scheduler
    return scheduler.status


@router.get("/recommendations/{asset}")
def get_recommendations(asset: str, top_k: int = 3):
    try:
        from app.core.recommender import recommend_strategies
    except ImportError:
        raise HTTPException(status_code=503, detail="recommender_unavailable_in_public_build")
    recs = recommend_strategies(asset=asset, top_k=top_k)
    return [
        {
            "name": r.name,
            "description": r.description,
            "asset_type": r.asset_type,
            "indicators": r.indicators,
            "weights": r.weights,
            "thresholds": r.thresholds,
            "formula_name": r.formula_name,
            "regime": r.regime,
            "confidence": r.confidence,
            "reasoning": r.reasoning,
        }
        for r in recs
    ]
