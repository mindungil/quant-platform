import os

from fastapi import APIRouter, Header, HTTPException, Query

from app.db.repository import strategy_repository
from app.models.strategy import Strategy, StrategyCreate, StrategyStatusUpdate
from shared.health import check_sql, health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return health_payload(
        "strategy-registry",
        {
            "postgres": check_sql(
                "postgres",
                os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"),
            )
        },
    )


@router.post("/strategies", response_model=Strategy)
def create_strategy(payload: StrategyCreate, x_user_id: str | None = Header(default=None)) -> Strategy:
    if x_user_id is not None:
        payload.user_id = x_user_id
    strategy = strategy_repository.create(payload)
    return strategy


@router.get("/strategies", response_model=list[Strategy])
def list_strategies(
    asset_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    x_user_id: str | None = Header(default=None),
) -> list[Strategy]:
    effective_user_id = user_id or x_user_id
    return strategy_repository.list_strategies(
        asset_type=asset_type,
        status=status,
        user_id=effective_user_id,
    )


@router.get("/strategies/active", response_model=Strategy)
def get_active_strategy(asset_type: str, x_user_id: str | None = Header(default=None)) -> Strategy:
    strategy = strategy_repository.get_active_for_user(asset_type, x_user_id or "anonymous")
    if strategy is None:
        raise HTTPException(status_code=404, detail="active_strategy_not_found")
    return strategy


@router.get("/strategies/{strategy_id}", response_model=Strategy)
def get_strategy(strategy_id: str, x_user_id: str | None = Header(default=None)) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None or (x_user_id is not None and strategy.user_id not in {x_user_id, "bootstrap"}):
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return strategy


@router.patch("/strategies/{strategy_id}/status", response_model=Strategy)
def update_status(
    strategy_id: str, payload: StrategyStatusUpdate, x_user_id: str | None = Header(default=None)
) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None or (x_user_id is not None and strategy.user_id != x_user_id):
        raise HTTPException(status_code=404, detail="strategy_not_found")
    if not strategy_repository.validate_transition(strategy.status, payload.status):
        raise HTTPException(
            status_code=409,
            detail=f"invalid_transition: {strategy.status} -> {payload.status}",
        )
    if payload.status == "ACTIVE" and strategy.status == "DRAFT":
        bt = strategy.backtest_results or {}
        if bt.get("status") != "PASSED" and bt.get("source") != "bootstrap_seed":
            raise HTTPException(
                status_code=409,
                detail="backtest_not_passed: DRAFT->ACTIVE requires backtest PASSED",
            )
    strategy = strategy_repository.update_status(strategy_id, payload.status)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return strategy


@router.delete("/strategies/{strategy_id}", response_model=Strategy)
def delete_strategy(strategy_id: str, x_user_id: str | None = Header(default=None)) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None or (x_user_id is not None and strategy.user_id != x_user_id):
        raise HTTPException(status_code=404, detail="strategy_not_found")
    if strategy.status == "ARCHIVED":
        return strategy
    if not strategy_repository.validate_transition(strategy.status, "ARCHIVED"):
        raise HTTPException(
            status_code=409,
            detail=f"invalid_transition: {strategy.status} -> ARCHIVED",
        )
    strategy = strategy_repository.update_status(strategy_id, "ARCHIVED")
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return strategy
