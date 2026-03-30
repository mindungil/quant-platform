from fastapi import APIRouter
from app.core.engine import compute_statistics
from app.models.statistics import StatisticsInput, StatisticsSnapshot

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/statistics/compute", response_model=StatisticsSnapshot)
def compute(payload: StatisticsInput) -> StatisticsSnapshot:
    return compute_statistics(payload)
