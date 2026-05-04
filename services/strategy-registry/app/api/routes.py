from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from app.db.repository import strategy_repository
from app.db.subscription_repository import subscription_repository
from app.models.strategy import Strategy, StrategyCreate, StrategyStatusUpdate, ShadowMetricsUpdate
from app.models.subscription import (
    LaneAllocation,
    LaneAllocationUpdate,
    TemplateSubscription,
    TemplateSubscriptionCreate,
    TemplateSubscriptionUpdate,
)
from shared.health import check_sql, health_payload
from shared.internal_admin import require_internal_admin, verify_internal_admin_headers

router = APIRouter()


def _internal_admin_secret() -> str:
    return os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret")


def _admin_header_ttl_seconds() -> int:
    return int(os.getenv("ADMIN_HEADER_TTL_SECONDS", "300"))


def _is_backtest_passed(bt: dict | None) -> bool:
    """A strategy may transition to ACTIVE / SHADOW only when its backtest
    payload says PASSED *and* carries actual metrics. We require:
      - status == "PASSED"
      - metrics.sharpe present and finite
      - metrics.n_obs >= 250 (one trading year of hourly bars or ~1y daily)

    The previous "source==bootstrap_seed bypass" allowed seed strategies to
    walk straight to ACTIVE with no evidence — removed.
    """
    if not isinstance(bt, dict) or bt.get("status") != "PASSED":
        return False
    metrics = bt.get("metrics") or {}
    sharpe = metrics.get("sharpe")
    n_obs = metrics.get("n_obs", 0)
    if sharpe is None:
        return False
    try:
        sharpe = float(sharpe)
    except (TypeError, ValueError):
        return False
    if sharpe != sharpe or sharpe == float("inf") or sharpe == float("-inf"):
        return False
    return int(n_obs or 0) >= 250


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


@router.get("/metrics")
def metrics() -> Response:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
def list_shadow_strategies(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[Strategy]:
    """List all strategies currently in SHADOW status with their metrics."""
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    return strategy_repository.get_shadow_strategies()


# Templates routes MUST be declared before /strategies/{strategy_id} so the
# literal "templates" path segment is not captured as a strategy_id.
@router.get("/strategies/templates")
def list_templates(category: str | None = Query(default=None)) -> list[dict]:
    """List Korean strategy templates, optionally filtered by category."""
    from shared.strategy_templates import get_all_templates, get_by_category

    if category:
        return get_by_category(category)
    return get_all_templates()


@router.get("/strategies/templates/{template_id}")
def get_template_detail(template_id: str) -> dict:
    """Fetch a single Korean strategy template."""
    from shared.strategy_templates import get_template

    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="template_not_found")
    return template


@router.post("/strategies/templates/{template_id}/activate", response_model=TemplateSubscription)
def activate_template(
    template_id: str,
    x_user_id: str | None = Header(default=None),
) -> TemplateSubscription:
    """Subscribe a user to a template (Template lane). v2 dual-lane: this no
    longer creates a DRAFT Strategy — it creates/flips a TemplateSubscription
    which the agent's Template lane reads at each tick."""
    from shared.strategy_templates import get_template

    user = _require_user(x_user_id)
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="template_not_found")
    return subscription_repository.create(
        user_id=user,
        template_id=template_id,
        asset_type=template.get("asset_type", "crypto"),
        weight=1.0,
    )


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
    strategy_id: str,
    payload: StrategyStatusUpdate,
    request: Request,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    _require_owner_or_internal_strategy(
        request=request,
        strategy=strategy,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    if not strategy_repository.validate_transition(strategy.status, payload.status):
        raise HTTPException(
            status_code=409,
            detail=f"invalid_transition: {strategy.status} -> {payload.status}",
        )
    # Strict promotion gating: a strategy may only become ACTIVE / SHADOW
    # if it carries a real PASSED backtest. The previous code allowed any
    # strategy whose source string was the literal "bootstrap_seed" to
    # bypass this check — that escape hatch is removed. Bootstrapped
    # strategies must now run a real shared.backtest.runner pass at seed
    # time and persist the metrics, just like every other strategy.
    if payload.status == "ACTIVE" and strategy.status == "DRAFT":
        if not _is_backtest_passed(strategy.backtest_results):
            raise HTTPException(
                status_code=409,
                detail="backtest_not_passed: DRAFT->ACTIVE requires PASSED backtest with metrics",
            )
    if payload.status == "SHADOW" and strategy.status == "TESTED":
        if not _is_backtest_passed(strategy.backtest_results):
            raise HTTPException(
                status_code=409,
                detail="backtest_not_passed: TESTED->SHADOW requires PASSED backtest with metrics",
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
    strategy_id: str,
    payload: ShadowMetricsUpdate,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> Strategy:
    """Update shadow metrics for a SHADOW strategy (called by agent after shadow trades)."""
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
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
    request: Request,
    min_days: int = Query(default=14),
    min_trades: int = Query(default=10),
    min_sharpe: float = Query(default=0.5),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    """Check if a SHADOW strategy should be promoted to ACTIVE or deprecated."""
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
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
def attach_backtest(
    strategy_id: str,
    payload: dict,
    request: Request,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> Strategy:
    """Attach backtest results to a strategy so it can be activated."""
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    _require_owner_or_internal_strategy(
        request=request,
        strategy=strategy,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    strategy.backtest_results = payload
    strategy_repository._persist(strategy)
    return strategy


@router.patch("/strategies/{strategy_id}/kelly-params", response_model=Strategy)
def update_kelly_params(
    strategy_id: str,
    payload: dict,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> Strategy:
    """Store Kelly parameters from backtest results into strategy.backtest_results (merge)."""
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    existing = strategy.backtest_results or {}
    existing.update(payload)
    strategy.backtest_results = existing
    strategy_repository._persist(strategy)
    return strategy


@router.post("/strategies/backtest-callback")
def backtest_callback(
    payload: dict,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    """Receive backtest results and apply auto-transition rules.

    Rules:
    - PENDING → TESTED if sharpe > 0.5
    - TESTED → SHADOW if sharpe > 1.0
    """
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )

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
def delete_strategy(
    strategy_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> Strategy:
    strategy = strategy_repository.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_not_found")
    _require_owner_or_internal_strategy(
        request=request,
        strategy=strategy,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
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
# Template subscriptions (Template lane) — dual-lane design
# ---------------------------------------------------------------------------

def _require_user(x_user_id: str | None) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing_user")
    return x_user_id


def _is_internal(
    *,
    request: Request,
    actor_user_id: str | None,
    timestamp: str | None,
    signature: str | None,
) -> bool:
    return bool(verify_internal_admin_headers(
        secret=_internal_admin_secret(),
        path=str(request.url.path),
        actor_user_id=actor_user_id,
        timestamp=timestamp,
        signature=signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    ))


def _require_owner_or_internal_strategy(
    *,
    request: Request,
    strategy: Strategy,
    x_user_id: str | None,
    x_internal_actor_user_id: str | None,
    x_internal_admin_timestamp: str | None,
    x_internal_admin_signature: str | None,
) -> None:
    if _is_internal(
        request=request,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
    ):
        return
    if x_user_id is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if strategy.user_id not in {x_user_id, "bootstrap"}:
        raise HTTPException(status_code=404, detail="strategy_not_found")


@router.get("/templates/subscriptions", response_model=list[TemplateSubscription])
def list_subscriptions(
    asset_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    x_user_id: str | None = Header(default=None),
) -> list[TemplateSubscription]:
    user = _require_user(x_user_id)
    return subscription_repository.list_for_user(user, asset_type=asset_type, status=status)


@router.get("/templates/subscriptions/all", response_model=list[TemplateSubscription])
def list_all_enabled_subscriptions(
    request: Request,
    asset_type: str | None = Query(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[TemplateSubscription]:
    """Internal: flat list of all enabled subscriptions for agent fan-out.

    Not user-scoped — used by crypto-agent's dual-lane orchestrator to
    iterate over users with active template subscriptions. In production
    this should be gated to service-to-service callers via internal network
    isolation; the gateway does not expose this path publicly.
    """
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    return subscription_repository.list_all_enabled(asset_type=asset_type)


@router.post("/templates/subscriptions", response_model=TemplateSubscription)
def create_subscription(
    payload: TemplateSubscriptionCreate,
    x_user_id: str | None = Header(default=None),
) -> TemplateSubscription:
    user = _require_user(x_user_id)
    from shared.strategy_templates import get_template

    if get_template(payload.template_id) is None:
        raise HTTPException(status_code=404, detail="template_not_found")
    return subscription_repository.create(
        user_id=user,
        template_id=payload.template_id,
        asset_type=payload.asset_type,
        weight=payload.weight,
    )


@router.patch("/templates/subscriptions/{subscription_id}", response_model=TemplateSubscription)
def update_subscription(
    subscription_id: str,
    payload: TemplateSubscriptionUpdate,
    x_user_id: str | None = Header(default=None),
) -> TemplateSubscription:
    user = _require_user(x_user_id)
    existing = subscription_repository.get(subscription_id)
    if existing is None or existing.user_id != user:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    updated = subscription_repository.update(
        subscription_id, status=payload.status, weight=payload.weight
    )
    if updated is None:
        raise HTTPException(status_code=400, detail="invalid_update")
    return updated


@router.delete("/templates/subscriptions/{subscription_id}")
def delete_subscription(
    subscription_id: str,
    x_user_id: str | None = Header(default=None),
) -> dict:
    user = _require_user(x_user_id)
    existing = subscription_repository.get(subscription_id)
    if existing is None or existing.user_id != user:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    subscription_repository.delete(subscription_id)
    return {"ok": True, "subscription_id": subscription_id}


@router.get("/settings/lane-allocation", response_model=LaneAllocation)
def get_lane_allocation(
    asset_type: str = Query(default="crypto"),
    x_user_id: str | None = Header(default=None),
) -> LaneAllocation:
    user = _require_user(x_user_id)
    return subscription_repository.get_allocation(user, asset_type)


@router.patch("/settings/lane-allocation", response_model=LaneAllocation)
def update_lane_allocation(
    payload: LaneAllocationUpdate,
    x_user_id: str | None = Header(default=None),
) -> LaneAllocation:
    user = _require_user(x_user_id)
    try:
        return subscription_repository.upsert_allocation(
            user, payload.asset_type, payload.agent_pct, payload.template_pct
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
