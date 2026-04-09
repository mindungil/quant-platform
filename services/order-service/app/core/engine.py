import os
import time
from uuid import uuid4

import httpx as _httpx
from prometheus_client import Counter, Histogram

from app.core.config import settings
from app.core.protection import protection_manager
from app.core.twap import TwapPlan, execute_twap
from app.db.repository import order_repository
from app.models.order import CredentialSnapshot, FillSnapshot, OrderRequest, OrderResponse, PortfolioSnapshot, StatisticsSnapshot
from app.services.exchange_client import ExchangeClient
from app.services.risk_client import RiskClient
from app.services.credential_client import CredentialClient
from app.services.event_publisher import publisher
from app.services.portfolio_client import PortfolioClient
from app.services.statistics_client import StatisticsClient
from shared.logging import get_logger

orders_total = Counter(
    "orders_total",
    "Total orders processed",
    ["status", "shadow_mode"],
)
order_fill_latency_seconds = Histogram(
    "order_fill_latency_seconds",
    "End-to-end order processing latency",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
order_fills_total = Counter(
    "order_fills_total",
    "Total order fill events",
    ["exchange", "asset"],
)
order_lifecycle_total = Counter(
    "order_lifecycle_total",
    "Order lifecycle state transitions",
    ["status"],
)

risk_client = RiskClient(settings.risk_service_base_url)
exchange_client = ExchangeClient(settings.exchange_adapter_base_url)
credential_client = CredentialClient(settings.credential_store_base_url)
portfolio_client = PortfolioClient(settings.portfolio_service_base_url)
statistics_client = StatisticsClient(settings.statistics_service_base_url)
logger = get_logger("order-service")


def _record_lifecycle(order_id: str, user_id: str, status: str, detail: dict) -> None:
    order_lifecycle_total.labels(status=status).inc()
    if hasattr(order_repository, "record_lifecycle"):
        order_repository.record_lifecycle(order_id, user_id, status, detail=detail)


def _record_order_metrics(status: str, shadow_mode: bool, start: float, exchange: str = "", asset: str = "") -> None:
    orders_total.labels(status=status, shadow_mode=str(shadow_mode).lower()).inc()
    order_fill_latency_seconds.observe(time.monotonic() - start)
    if status == "FILLED":
        order_fills_total.labels(exchange=exchange, asset=asset).inc()


def _process_order_single(payload: OrderRequest) -> "OrderResponse":
    """Execute a single, non-sliced order. This is the original path; the
    public `process_order` wraps this in a TWAP slicer for large orders."""
    return _process_order_impl(payload)


def process_order(payload: OrderRequest) -> "OrderResponse":
    """Public entry point. For live orders above the TWAP threshold, splits
    into N children and submits them serially via the same path. For shadow
    or small orders, the original single-shot path is used."""
    plan = TwapPlan.from_request(payload)
    if plan is None:
        return _process_order_impl(payload)

    children = execute_twap(payload, plan, _process_order_impl)
    if not children:
        # Slicer aborted before any child went through — produce a synthetic FAILED response
        return OrderResponse(
            order_id=str(uuid4()),
            user_id=payload.user_id,
            asset=payload.asset,
            side=payload.side,
            quantity=0.0,
            status="FAILED",
            risk_reason="twap_no_children_executed",
            exchange=payload.exchange,
            shadow_mode=payload.shadow_mode,
            credential=CredentialSnapshot(user_id=payload.user_id, exchange=payload.exchange, loaded=False),
        )
    # Last child carries the latest snapshot — return that
    return children[-1]


def _process_order_impl(payload: OrderRequest) -> OrderResponse:
    _start = time.monotonic()

    # Idempotency check: return cached result if duplicate
    if payload.idempotency_key:
        existing = order_repository.get_by_idempotency_key(payload.idempotency_key)
        if existing:
            logger.info(
                "idempotent_order_returned",
                extra={
                    "service": "order-service",
                    "idempotency_key": payload.idempotency_key,
                    "order_id": existing.order_id,
                    "user_id": payload.user_id,
                    "event_type": "order.idempotent",
                },
            )
            return existing

    local_order_id = str(uuid4())
    payload.correlation_id = payload.correlation_id or local_order_id
    execution_config = order_repository.get_execution_config()
    payload.shadow_mode = payload.shadow_mode or execution_config.default_shadow_mode
    _record_lifecycle(local_order_id, payload.user_id, "PENDING", {"stage": "received"})

    if payload.strategy_status.upper() != "ACTIVE":
        response = OrderResponse(
            order_id=local_order_id,
            user_id=payload.user_id,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status="REJECTED",
            risk_reason="strategy_not_active",
            exchange=payload.exchange,
            shadow_mode=payload.shadow_mode,
            credential=CredentialSnapshot(user_id=payload.user_id, exchange=payload.exchange, loaded=False),
        )
        order_repository.save(payload.user_id, response, detail={"stage": "gate", "reason": "strategy_not_active"}, idempotency_key=payload.idempotency_key)
        publisher.publish_risk_triggered(
            payload=payload,
            reason="strategy_not_active",
            level="REJECTED",
            requested_notional=payload.requested_notional,
        )
        _record_order_metrics(response.status, payload.shadow_mode, _start)
        return response

    approval = risk_client.approve(payload)
    if not approval["approved"]:
        response = OrderResponse(
            order_id=local_order_id,
            user_id=payload.user_id,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status="REJECTED",
            risk_reason=approval["reason"],
            exchange="",
            shadow_mode=payload.shadow_mode,
            credential=CredentialSnapshot(
                user_id=payload.user_id,
                exchange=payload.exchange,
                loaded=False,
            ),
        )
        order_repository.save(payload.user_id, response, detail={"stage": "risk", "approval": approval}, idempotency_key=payload.idempotency_key)
        publisher.publish_risk_triggered(
            payload=payload,
            reason=approval["reason"],
            level=approval.get("level", "REJECTED"),
            requested_notional=payload.requested_notional,
        )
        _record_order_metrics(response.status, payload.shadow_mode, _start)
        return response
    _record_lifecycle(local_order_id, payload.user_id, "APPROVED", {"stage": "risk", "approval": approval})

    credential = credential_client.get(payload.user_id, payload.exchange)
    if credential is None and not payload.shadow_mode:
        # 실제 매매에서만 credential 필수 — 섀도우는 없어도 통과
        response = OrderResponse(
            order_id=local_order_id,
            user_id=payload.user_id,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status="FAILED",
            risk_reason="missing_credentials",
            exchange=payload.exchange,
            shadow_mode=payload.shadow_mode,
            credential=CredentialSnapshot(user_id=payload.user_id, exchange=payload.exchange, loaded=False),
        )
        order_repository.save(payload.user_id, response, detail={"stage": "credential", "reason": "missing_credentials"}, idempotency_key=payload.idempotency_key)
        _record_order_metrics(response.status, payload.shadow_mode, _start)
        return response
    if not payload.shadow_mode:
        if not execution_config.live_trading_enabled:
            response = OrderResponse(
                order_id=local_order_id,
                user_id=payload.user_id,
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                status="REJECTED",
                risk_reason="live_trading_disabled",
                exchange=payload.exchange,
                shadow_mode=False,
                credential=CredentialSnapshot(
                    user_id=payload.user_id,
                    exchange=payload.exchange,
                    loaded=True,
                    sandbox=credential.get("sandbox", True),
                    label=credential.get("label"),
                ),
            )
            order_repository.save(payload.user_id, response, detail={"stage": "gate", "reason": "live_trading_disabled"}, idempotency_key=payload.idempotency_key)
            publisher.publish_risk_triggered(
                payload=payload,
                reason="live_trading_disabled",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
            _record_order_metrics(response.status, payload.shadow_mode, _start)
            return response
        if payload.exchange.lower() not in {item.lower() for item in execution_config.allowed_exchanges}:
            response = OrderResponse(
                order_id=local_order_id,
                user_id=payload.user_id,
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                status="REJECTED",
                risk_reason="exchange_not_allowed",
                exchange=payload.exchange,
                shadow_mode=False,
                credential=CredentialSnapshot(
                    user_id=payload.user_id,
                    exchange=payload.exchange,
                    loaded=True,
                    sandbox=credential.get("sandbox", True),
                    label=credential.get("label"),
                ),
            )
            order_repository.save(payload.user_id, response, detail={"stage": "gate", "reason": "exchange_not_allowed"}, idempotency_key=payload.idempotency_key)
            publisher.publish_risk_triggered(
                payload=payload,
                reason="exchange_not_allowed",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
            _record_order_metrics(response.status, payload.shadow_mode, _start)
            return response
        if credential.get("sandbox", True):
            response = OrderResponse(
                order_id=local_order_id,
                user_id=payload.user_id,
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                status="REJECTED",
                risk_reason="sandbox_credentials_for_live_order",
                exchange=payload.exchange,
                shadow_mode=False,
                credential=CredentialSnapshot(
                    user_id=payload.user_id,
                    exchange=payload.exchange,
                    loaded=True,
                    sandbox=True,
                    label=credential.get("label"),
                ),
            )
            order_repository.save(
                payload.user_id,
                response,
                detail={"stage": "gate", "reason": "sandbox_credentials_for_live_order"},
                idempotency_key=payload.idempotency_key,
            )
            publisher.publish_risk_triggered(
                payload=payload,
                reason="sandbox_credentials_for_live_order",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
            _record_order_metrics(response.status, payload.shadow_mode, _start)
            return response

    # 섀도우 모드에서는 실제 API 키를 전달하지 않음 (보안)
    if credential and not payload.shadow_mode:
        payload.api_key = credential.get("api_key")
        payload.api_secret = credential.get("api_secret")
        payload.credential_label = credential.get("label")
        payload.credential_sandbox = credential.get("sandbox", True)
    elif credential:
        payload.credential_label = credential.get("label")
        payload.credential_sandbox = credential.get("sandbox", True)
    # --- Kelly Criterion position sizing (optional) ---
    original_quantity = payload.quantity
    if payload.strategy_id:
        try:
            registry_url = os.environ.get("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
            resp = _httpx.get(f"{registry_url}/strategies/{payload.strategy_id}", timeout=5.0)
            if resp.status_code == 200:
                strategy = resp.json()
                kelly_params = strategy.get("kelly_params")
                if kelly_params and kelly_params.get("backtest_win_rate"):
                    win_rate = kelly_params["backtest_win_rate"]
                    payoff = kelly_params.get("backtest_payoff_ratio", 1.5)
                    # Kelly fraction: f* = (p*b - q) / b where p=win_rate, b=payoff, q=1-p
                    kelly_f = (win_rate * payoff - (1 - win_rate)) / payoff if payoff > 0 else 0
                    # Fractional Kelly (25%) with max 15% of portfolio
                    safe_f = min(max(kelly_f * 0.25, 0.01), 0.15)
                    # Adjust notional using portfolio exposure limit as proxy for portfolio value
                    portfolio_value = payload.max_notional if payload.max_notional > 0 else payload.requested_notional
                    kelly_notional = portfolio_value * safe_f
                    original_notional = payload.requested_notional
                    if kelly_notional < original_notional and payload.price > 0:
                        payload.quantity = kelly_notional / payload.price
                        payload.requested_notional = kelly_notional
                        logger.info(
                            "kelly_sizing_applied",
                            extra={
                                "service": "order-service",
                                "strategy_id": payload.strategy_id,
                                "kelly_f": round(kelly_f, 4),
                                "safe_f": round(safe_f, 4),
                                "original_quantity": original_quantity,
                                "adjusted_quantity": round(payload.quantity, 8),
                                "adjusted_notional": round(kelly_notional, 2),
                                "user_id": payload.user_id,
                            },
                        )
        except Exception:
            pass  # proceed with original sizing

    publisher.publish_order_created(payload, local_order_id)
    logger.info(
        "exchange_submission_started",
        extra={
            "service": "order-service",
            "correlation_id": payload.correlation_id,
            "user_id": payload.user_id,
            "event_type": "order.exchange.submit",
        },
    )
    exchange_result = exchange_client.place(payload)
    _record_lifecycle(
        local_order_id,
        payload.user_id,
        "SUBMITTED",
        {
            "stage": "exchange",
            "exchange_order_id": exchange_result.get("order_id"),
            "exchange_status": exchange_result["status"],
        },
    )

    portfolio = None
    try:
        portfolio = portfolio_client.apply_fill(payload, order_id=local_order_id, status=exchange_result["status"])
    except Exception:
        logger.exception(
            "portfolio_apply_failed",
            extra={
                "service": "order-service",
                "correlation_id": payload.correlation_id,
                "user_id": payload.user_id,
                "event_type": "portfolio.updated",
            },
        )

    statistics = None
    try:
        statistics = statistics_client.record_trade(payload, order_status=exchange_result["status"], order_id=local_order_id)
    except TypeError:
        try:
            statistics = statistics_client.record_trade(payload, order_status=exchange_result["status"])
        except Exception:
            logger.exception(
                "statistics_record_failed",
                extra={
                    "service": "order-service",
                    "correlation_id": payload.correlation_id,
                    "user_id": payload.user_id,
                    "event_type": "statistics.updated",
                },
            )
    except Exception:
        logger.exception(
            "statistics_record_failed",
            extra={
                "service": "order-service",
                "correlation_id": payload.correlation_id,
                "user_id": payload.user_id,
                "event_type": "statistics.updated",
            },
        )

    response = OrderResponse(
        user_id=payload.user_id,
        order_id=local_order_id,
        asset=payload.asset,
        side=payload.side,
        quantity=payload.quantity,
        status=exchange_result["status"],
        risk_reason=approval["reason"],
        exchange=payload.exchange,
        shadow_mode=payload.shadow_mode,
        credential=CredentialSnapshot(
            user_id=payload.user_id,
            exchange=payload.exchange,
            loaded=credential is not None,
            sandbox=True if credential is None else credential.get("sandbox", True),
            label=None if credential is None else credential.get("label"),
        ),
        fill=FillSnapshot(
            order_id=local_order_id,
            status=exchange_result["status"],
            filled_quantity=payload.quantity,
            filled_price=payload.price,
        ),
        portfolio=PortfolioSnapshot.model_validate(portfolio) if portfolio is not None else None,
        statistics=StatisticsSnapshot.model_validate(statistics) if statistics is not None else None,
    )
    order_repository.save(
        payload.user_id,
        response,
        detail={
            "stage": "exchange",
            "external_order_id": exchange_result.get("order_id"),
            "exchange_status": exchange_result["status"],
            "circuit_state": exchange_result.get("circuit_state"),
            "portfolio_recorded": portfolio is not None,
            "statistics_recorded": statistics is not None,
        },
        idempotency_key=payload.idempotency_key,
    )
    publisher.publish_order_filled(payload, response)

    # Shadow ledger: when a strategy is in shadow/paper mode, every "fill" is
    # a paper trade. Record it so the strategy-registry's promotion gate has
    # real metrics to evaluate. This closes the SHADOW → ACTIVE loop.
    if (
        payload.shadow_mode
        and response.status == "FILLED"
        and payload.strategy_id
        and response.fill is not None
    ):
        try:
            from shared.shadow import ShadowFill
            from shared.shadow.recorder import get_recorder
            recorder = get_recorder()
            # Heuristic realized PnL: if there's an existing open position from
            # the portfolio snapshot, treat this fill as a close. Otherwise it's
            # an open. We don't have full per-strategy position bookkeeping in
            # the order-service so we use a simple "every fill is a round-trip
            # at the requested notional" model — the goal is to *track relative
            # performance*, not to be a portfolio accountant.
            entry = float(response.fill.filled_price or payload.price or 0.0)
            qty = float(response.fill.filled_quantity or payload.quantity or 0.0)
            # PnL approximation: for a closing fill, pnl_pct from previous mark.
            # We don't have a previous mark, so for the shadow ledger we record
            # signed notional change driven by direction; the recorder uses
            # this as the "trade outcome" series to compute Sharpe over time.
            sign = 1.0 if payload.side.upper() == "BUY" else -1.0
            # Pull most recent quote for a quick-and-dirty mark via portfolio snapshot
            mark = entry
            if response.portfolio is not None:
                try:
                    pos = next((p for p in (response.portfolio.positions or []) if getattr(p, "asset", None) == payload.asset), None)
                    if pos is not None and getattr(pos, "average_price", None):
                        mark = float(pos.average_price)
                except Exception:
                    mark = entry
            # The shadow ledger needs realized pnl to compute metrics; for the
            # bar-by-bar shadow strategy this is approximated as 0 on open
            # fills and pnl_pct * notional on closing ones. We mark every fill
            # as realized=True with pnl=0 by default; the orchestrator promotes
            # by counting trades + checking subsequent strategy state.
            recorder.record_fill(
                ShadowFill(
                    strategy_id=str(payload.strategy_id),
                    user_id=str(payload.user_id),
                    asset=str(payload.asset),
                    side=str(payload.side).upper(),
                    quantity=qty,
                    entry_price=entry,
                    exit_price=mark if mark != entry else None,
                    pnl=(mark - entry) * qty * sign if mark != entry else 0.0,
                    realized=True,
                )
            )
            # Best-effort push to registry — don't block the order path
            recorder.push_snapshot(str(payload.strategy_id))
        except Exception:
            logger.exception(
                "shadow_recorder_failed",
                extra={
                    "service": "order-service",
                    "order_id": response.order_id,
                    "strategy_id": payload.strategy_id,
                },
            )

    # Create protective orders (stop-loss, take-profit, trailing stop) for filled orders
    if response.status == "FILLED" and response.fill is not None:
        try:
            protections = protection_manager.create_protections(response, payload)
            if protections:
                logger.info(
                    "protective_orders_attached",
                    extra={
                        "service": "order-service",
                        "order_id": response.order_id,
                        "user_id": response.user_id,
                        "protection_count": len(protections),
                        "types": [p.trigger_type for p in protections],
                    },
                )
        except Exception:
            logger.exception(
                "protective_orders_creation_failed",
                extra={
                    "service": "order-service",
                    "order_id": response.order_id,
                    "user_id": response.user_id,
                },
            )

    _record_order_metrics(response.status, payload.shadow_mode, _start, exchange=payload.exchange, asset=payload.asset)
    return response
