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


@router.get("/recommendations/{asset}")
def get_recommendations(asset: str, top_k: int = 3):
    from app.core.recommender import recommend_strategies
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
