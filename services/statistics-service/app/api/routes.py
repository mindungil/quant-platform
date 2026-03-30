from fastapi import APIRouter
from app.core.engine import compute_statistics
from app.db.repository import statistics_repository
from app.models.statistics import StatisticsInput, StatisticsSnapshot

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/statistics/compute", response_model=StatisticsSnapshot)
def compute(payload: StatisticsInput) -> StatisticsSnapshot:
    return compute_statistics(payload)


@router.post("/statistics/record", response_model=StatisticsSnapshot)
def record_trade(payload: StatisticsInput) -> StatisticsSnapshot:
    if payload.user_id is None:
        return compute_statistics(payload)
    pnl = payload.trade_pnls[-1] if payload.trade_pnls else 0.0
    return statistics_repository.record_trade(payload.user_id, pnl, payload.expected_return)


@router.get("/statistics/{user_id}", response_model=StatisticsSnapshot)
def get_statistics(user_id: str) -> StatisticsSnapshot:
    return statistics_repository.get(user_id)
