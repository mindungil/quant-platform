from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException, Query

from app.db.repository import strategy_repository
from app.models.strategy import Strategy, StrategyCreate, StrategyStatusUpdate, ShadowMetricsUpdate
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
    effective_user = x_user_id or "anonymous"
    strategy = strategy_repository.get_active_for_user(asset_type, effective_user)
    if strategy is None:
        raise HTTPException(status_code=404, detail="active_strategy_not_found")
    return strategy


@router.get("/strategies/shadow", response_model=list[Strategy])
def list_shadow_strategies() -> list[Strategy]:
    """List all strategies currently in SHADOW status with their metrics."""
    return strategy_repository.get_shadow_strategies()


@router.get("/strategies/{strategy_id}", response_model=Strategy)
def get_strategy(strategy_id: str, x_user_id: str | None = Header(default=None)) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    if x_user_id is None and strategy.user_id != "bootstrap":
        raise HTTPException(status_code=403, detail="forbidden")
    if x_user_id is not None and strategy.user_id not in {x_user_id, "bootstrap"}:
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
    if payload.status == "SHADOW" and strategy.status == "TESTED":
        bt = strategy.backtest_results or {}
        if bt.get("status") != "PASSED" and bt.get("source") != "bootstrap_seed":
            raise HTTPException(
                status_code=409,
                detail="backtest_not_passed: TESTED->SHADOW requires backtest PASSED",
            )
    strategy = strategy_repository.update_status(strategy_id, payload.status)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return strategy


# ---------------------------------------------------------------------------
# Shadow lifecycle endpoints
# ---------------------------------------------------------------------------


@router.post("/strategies/{strategy_id}/shadow/metrics", response_model=Strategy)
def update_shadow_metrics(
    strategy_id: str, payload: ShadowMetricsUpdate, x_user_id: str | None = Header(default=None)
) -> Strategy:
    """Update shadow metrics for a SHADOW strategy (called by agent after shadow trades)."""
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    if strategy.status != "SHADOW":
        raise HTTPException(status_code=409, detail="strategy_not_in_shadow")
    updated = strategy_repository.update_shadow_metrics(strategy_id, payload.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    return updated


@router.post("/strategies/{strategy_id}/shadow/promote")
def promote_shadow(
    strategy_id: str,
    min_days: int = Query(default=14),
    min_trades: int = Query(default=10),
    min_sharpe: float = Query(default=0.5),
) -> dict:
    """Check if a SHADOW strategy should be promoted to ACTIVE or deprecated."""
    outcome, strategy = strategy_repository.promote_shadow_if_ready(
        strategy_id, min_days=min_days, min_trades=min_trades, min_sharpe=min_sharpe
    )
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="strategy_not_found_or_not_shadow")
    result = {"outcome": outcome, "strategy_id": strategy_id}
    if strategy is not None:
        result["status"] = strategy.status
        result["shadow_metrics"] = strategy.shadow_metrics
    return result


@router.patch("/strategies/{strategy_id}/backtest", response_model=Strategy)
def attach_backtest(strategy_id: str, payload: dict, x_user_id: str | None = Header(default=None)) -> Strategy:
    """Attach backtest results to a strategy so it can be activated."""
    strategy = strategy_repository.get(strategy_id)
    if strategy is None or (x_user_id is not None and strategy.user_id not in {x_user_id, "bootstrap"}):
        raise HTTPException(status_code=404, detail="strategy_not_found")
    strategy.backtest_results = payload
    strategy_repository._persist(strategy)
    return strategy


@router.patch("/strategies/{strategy_id}/kelly-params", response_model=Strategy)
def update_kelly_params(strategy_id: str, payload: dict) -> Strategy:
    """Store Kelly parameters from backtest results into strategy.backtest_results (merge)."""
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    existing = strategy.backtest_results or {}
    existing.update(payload)
    strategy.backtest_results = existing
    strategy_repository._persist(strategy)
    return strategy


@router.post("/strategies/backtest-callback")
def backtest_callback(payload: dict) -> dict:
    """Receive backtest results and apply auto-transition rules.

    Rules:
    - PENDING → TESTED if sharpe > 0.5
    - TESTED → SHADOW if sharpe > 1.0
    """
    strategy_id = payload.get("strategy_id")
    sharpe = float(payload.get("sharpe_ratio", 0))

    if not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id required")

    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")

    new_status = None
    if strategy.status == "PENDING" and sharpe > 0.5:
        new_status = "TESTED"
    elif strategy.status == "TESTED" and sharpe > 1.0:
        new_status = "SHADOW"

    if new_status and strategy_repository.validate_transition(strategy.status, new_status):
        updated = strategy_repository.update_status(strategy_id, new_status)
        return {
            "strategy_id": strategy_id,
            "previous_status": strategy.status,
            "new_status": new_status,
            "sharpe_ratio": sharpe,
            "auto_transitioned": True,
        }

    return {
        "strategy_id": strategy_id,
        "status": strategy.status,
        "sharpe_ratio": sharpe,
        "auto_transitioned": False,
    }


@router.delete("/strategies/{strategy_id}", response_model=Strategy)
def delete_strategy(strategy_id: str, x_user_id: str | None = Header(default=None)) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    if x_user_id is None and strategy.user_id != "bootstrap":
        raise HTTPException(status_code=403, detail="forbidden")
    if x_user_id is not None and strategy.user_id != x_user_id:
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


# ---------------------------------------------------------------------------
# Quant Model Registry endpoints
# ---------------------------------------------------------------------------

@router.post("/models")
def register_model(payload: dict) -> dict:
    from app.core.model_registry import model_registry
    return model_registry.register(payload)


@router.get("/models")
def list_models(asset_type: str | None = None) -> list:
    from app.core.model_registry import model_registry
    return model_registry.list_models(asset_type)


@router.get("/models/{model_id}")
def get_model(model_id: str) -> dict:
    from app.core.model_registry import model_registry
    model = model_registry.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return model


@router.patch("/models/{model_id}/backtest")
def update_model_backtest(model_id: str, payload: dict) -> dict:
    from app.core.model_registry import model_registry
    result = model_registry.update_backtest_results(model_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return result


@router.post("/models/{model_id}/promote")
def promote_model(model_id: str) -> dict:
    from app.core.model_registry import model_registry
    result = model_registry.promote(model_id)
    if result is None:
        raise HTTPException(status_code=409, detail="cannot_promote")
    return result


@router.post("/models/{name}/rollback")
def rollback_model(name: str) -> dict:
    from app.core.model_registry import model_registry
    result = model_registry.rollback(name)
    if result is None:
        raise HTTPException(status_code=404, detail="no_previous_version")
    return result
