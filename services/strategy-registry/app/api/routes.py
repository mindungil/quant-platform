from fastapi import APIRouter, HTTPException

from app.db.repository import strategy_repository
from app.models.strategy import Strategy, StrategyCreate, StrategyStatusUpdate

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/strategies", response_model=Strategy)
def create_strategy(payload: StrategyCreate) -> Strategy:
    strategy = strategy_repository.create(payload)
    return strategy


@router.get("/strategies/active", response_model=Strategy)
def get_active_strategy(asset_type: str) -> Strategy:
    strategy = strategy_repository.get_active(asset_type)
    if strategy is None:
        raise HTTPException(status_code=404, detail="active_strategy_not_found")
    return strategy


@router.get("/strategies/{strategy_id}", response_model=Strategy)
def get_strategy(strategy_id: str) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return strategy


@router.patch("/strategies/{strategy_id}/status", response_model=Strategy)
def update_status(strategy_id: str, payload: StrategyStatusUpdate) -> Strategy:
    strategy = strategy_repository.update_status(strategy_id, payload.status)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return strategy
