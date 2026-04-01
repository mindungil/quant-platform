from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import compute_statistics
from app.core.config import settings
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
