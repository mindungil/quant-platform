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
from shared.internal_admin import build_internal_admin_headers
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


_compliance_gateway = None


def _is_filled_status(status: str | None) -> bool:
    normalized = (status or "").upper()
    return normalized in {"FILLED", "SIMULATED_FILLED", "PARTIALLY_FILLED"}


def _is_rejected_status(status: str | None) -> bool:
    normalized = (status or "").upper()
    return normalized.startswith("REJECTED") or normalized == "FAILED"


def _extract_fill_details(payload: OrderRequest, exchange_result: dict) -> tuple[float, float, float]:
    filled_quantity = float(exchange_result.get("filled_quantity", 0.0) or 0.0)
    fill_price = float(exchange_result.get("average_fill_price", 0.0) or 0.0)
    fees = float(exchange_result.get("fees", 0.0) or 0.0)
    status = (exchange_result.get("status") or "").upper()
    if status in {"FILLED", "SIMULATED_FILLED"} and filled_quantity <= 0:
        filled_quantity = float(payload.quantity or 0.0)
    if filled_quantity > 0 and fill_price <= 0:
        fill_price = float(payload.price or 0.0)
        if fill_price <= 0 and payload.quantity > 0 and payload.requested_notional > 0:
            fill_price = float(payload.requested_notional) / float(payload.quantity)
    return filled_quantity, fill_price, fees


def _get_compliance_gateway():
    """Lazy gateway init so tests can monkey-patch and so unreachable
    portfolio-service doesn't block module import."""
    global _compliance_gateway
    if _compliance_gateway is not None:
        return _compliance_gateway
    try:
        from shared.execution.compliance import (
            ComplianceGateway, ComplianceLimits, StateProvider,
        )

        portfolio_url = settings.portfolio_service_base_url

        class _Provider(StateProvider):
            def __init__(self):
                self._ttl = 5.0
                self._ts = 0.0
                self._eq = float(os.getenv("FALLBACK_EQUITY_USD", "10000"))
                self._eq_stale = False  # track whether equity is from fallback
                self._pos: dict[str, float] = {}
                self._kill = False

            def _refresh(self):
                now = time.monotonic()
                if now - self._ts < self._ttl:
                    return
                try:
                    r = _httpx.get(
                        f"{portfolio_url}/portfolio/summary",
                        headers=build_internal_admin_headers(
                            settings.internal_admin_secret,
                            "order-service",
                            "/portfolio/summary",
                        ),
                        timeout=2.0,
                    )
                    if r.status_code == 200:
                        data = r.json()
                        self._eq = float(data.get("equity", self._eq))
                        self._pos = {k: float(v) for k, v in (data.get("positions") or {}).items()}
                        self._kill = bool(data.get("kill_switch", False))
                except Exception:
                    pass
                self._ts = now

            def get_equity(self):
                self._refresh(); return self._eq

            def get_positions(self):
                self._refresh(); return dict(self._pos)

            def is_kill_switch_active(self):
                self._refresh(); return self._kill

        def _f(k, d): return float(os.getenv(k, d))
        limits = ComplianceLimits(
            max_gross_leverage=_f("COMPLIANCE_MAX_GROSS_LEV", 3.0),
            max_net_exposure=_f("COMPLIANCE_MAX_NET_EXP", 1.0),
            max_symbol_weight=_f("COMPLIANCE_MAX_SYMBOL_W", 0.30),
            max_rolling_turnover=_f("COMPLIANCE_MAX_TURNOVER", 5.0),
            max_order_notional=_f("COMPLIANCE_MAX_ORDER_USD", 100_000.0),
            min_order_notional=_f("COMPLIANCE_MIN_ORDER_USD", 10.0),
            max_order_qty_pct=_f("COMPLIANCE_MAX_ORDER_PCT", 0.10),
        )
        _compliance_gateway = ComplianceGateway(limits=limits, state_provider=_Provider())
    except Exception as exc:
        logger.warning("compliance_gateway_init_failed", extra={"error": str(exc)[:200]})
        _compliance_gateway = False
    return _compliance_gateway


def _compliance_check(payload: OrderRequest) -> dict | None:
    """Run compliance gateway.

    Fail-closed by default in live mode: if the gateway cannot be initialised
    or the check throws, the order is **blocked** (not silently passed).
    Shadow-mode orders are always fail-open so observation is not disrupted.

    Set COMPLIANCE_FAIL_CLOSED=false to revert to legacy fail-open behaviour.
    """
    if os.getenv("COMPLIANCE_ENABLED", "true").lower() != "true":
        return None

    fail_closed = os.getenv("COMPLIANCE_FAIL_CLOSED", "true").lower() == "true"

    gw = _get_compliance_gateway()
    if not gw:
        if fail_closed and not payload.shadow_mode:
            logger.error(
                "compliance_gateway_unavailable_blocking",
                extra={"service": "order-service", "asset": payload.asset,
                       "event_type": "order.compliance.fail_closed"},
            )
            return {"approved": False, "reason": "compliance_gateway_unavailable",
                    "warnings": [], "checks": {}}
        return None
    try:
        d = gw.check(
            symbol=payload.asset,
            side=payload.side,
            order_notional=float(payload.requested_notional),
        )
        return {
            "approved": d.approved,
            "reason": d.reason,
            "warnings": list(d.warnings),
            "checks": dict(d.checks),
        }
    except Exception as exc:
        logger.warning(
            "compliance_check_error",
            extra={"service": "order-service", "error": str(exc)[:200],
                   "asset": payload.asset, "event_type": "order.compliance.error"},
        )
        if fail_closed and not payload.shadow_mode:
            return {"approved": False, "reason": "compliance_check_exception",
                    "warnings": [], "checks": {"error": str(exc)[:200]}}
        return None


def _notify_drift_monitor(asset: str, response, intended_price: float | None = None) -> None:
    """Fire-and-forget: POST realized bar return to signal-service drift."""
    signal_url = os.getenv("SIGNAL_SERVICE_BASE_URL", getattr(settings, "signal_service_base_url", ""))
    if not signal_url:
        return
    try:
        fill = response.fill
        fill_price = float(fill.filled_price or 0)
        side_sign = 1.0 if response.side.upper() == "BUY" else -1.0
        # Use fill price vs intended (payload.price) as normalized return proxy.
        # This feeds drift monitor's z-score — needs return-like magnitude.
        # Full bar return requires prev_close which we don't have here; the
        # drift_feeder cron handles bar-level returns; this provides a
        # real-time tick for intra-bar awareness.
        if fill_price > 0 and intended_price and float(intended_price) > 0:
            intended = float(intended_price)
            price_move = (fill_price - intended) / intended
            trade_return = side_sign * price_move
        else:
            trade_return = 0.0
        _httpx.post(
            f"{signal_url}/signals/meta/drift/{asset}/observe",
            json={"trade_return": trade_return},
            timeout=2.0,
        )
    except Exception:
        pass  # Non-blocking: drift is observational


def _record_lifecycle(order_id: str, user_id: str, status: str, detail: dict) -> None:
    order_lifecycle_total.labels(status=status).inc()
    if hasattr(order_repository, "record_lifecycle"):
        order_repository.record_lifecycle(order_id, user_id, status, detail=detail)


def _record_order_metrics(status: str, shadow_mode: bool, start: float, exchange: str = "", asset: str = "") -> None:
    orders_total.labels(status=status, shadow_mode=str(shadow_mode).lower()).inc()
    order_fill_latency_seconds.observe(time.monotonic() - start)
    if _is_filled_status(status):
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
    _record_lifecycle(
        local_order_id,
        payload.user_id,
        "PENDING",
        {
            "stage": "received",
            "strategy_id": payload.strategy_id,
            "agent_name": payload.agent_name,
            "lane": payload.lane,
            "lane_budget_pct": payload.lane_budget_pct,
            "subscription_id": payload.subscription_id,
            "template_id": payload.template_id,
        },
    )

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

    # Compliance gate — institutional pre-trade limits (leverage, concentration,
    # turnover, kill-switch). Only binding in live mode; shadow orders log but
    # do not block so we can observe what the gate would have done.
    compliance_decision = _compliance_check(payload)
    if compliance_decision is not None and not compliance_decision["approved"]:
        if payload.shadow_mode:
            logger.warning(
                "compliance_would_block_shadow",
                extra={
                    "service": "order-service",
                    "order_id": local_order_id,
                    "user_id": payload.user_id,
                    "asset": payload.asset,
                    "reason": compliance_decision["reason"],
                    "checks": compliance_decision.get("checks", {}),
                    "event_type": "order.compliance.shadow_block",
                },
            )
        else:
            response = OrderResponse(
                order_id=local_order_id,
                user_id=payload.user_id,
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                status="REJECTED",
                risk_reason=f"compliance:{compliance_decision['reason']}",
                exchange="",
                shadow_mode=payload.shadow_mode,
                credential=CredentialSnapshot(user_id=payload.user_id, exchange=payload.exchange, loaded=False),
            )
            order_repository.save(
                payload.user_id, response,
                detail={"stage": "compliance", "decision": compliance_decision},
                idempotency_key=payload.idempotency_key,
            )
            publisher.publish_risk_triggered(
                payload=payload,
                reason=f"compliance:{compliance_decision['reason']}",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
            logger.warning(
                "compliance_blocked",
                extra={
                    "service": "order-service",
                    "order_id": local_order_id,
                    "user_id": payload.user_id,
                    "asset": payload.asset,
                    "reason": compliance_decision["reason"],
                    "checks": compliance_decision.get("checks", {}),
                    "event_type": "order.compliance.rejected",
                },
            )
            _record_order_metrics(response.status, payload.shadow_mode, _start)
            return response

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

    pre_fill_portfolio = None
    try:
        pre_fill_portfolio = portfolio_client.get_snapshot(payload.user_id)
    except Exception:
        logger.exception(
            "portfolio_snapshot_prefetch_failed",
            extra={
                "service": "order-service",
                "correlation_id": payload.correlation_id,
                "user_id": payload.user_id,
                "event_type": "portfolio.prefetch.failed",
            },
        )

    exchange_status = exchange_result["status"]
    filled_quantity, fill_price, fill_fees = _extract_fill_details(payload, exchange_result)
    fill_recorded = _is_filled_status(exchange_status) and filled_quantity > 0

    portfolio = None
    if fill_recorded:
        try:
            portfolio = portfolio_client.apply_fill(
                payload,
                order_id=local_order_id,
                status=exchange_status,
                fill_quantity=filled_quantity,
                fill_price=fill_price,
                filled_notional=filled_quantity * fill_price,
            )
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
        statistics = statistics_client.record_trade(
            payload,
            order_status=exchange_status,
            order_id=local_order_id,
            pre_fill_portfolio=pre_fill_portfolio,
            fill_quantity=filled_quantity,
            fill_price=fill_price,
        )
    except TypeError:
        try:
            statistics = statistics_client.record_trade(
                payload,
                order_status=exchange_result["status"],
                pre_fill_portfolio=pre_fill_portfolio,
                fill_quantity=filled_quantity,
                fill_price=fill_price,
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
        status=exchange_status,
        risk_reason=approval["reason"] if not _is_rejected_status(exchange_status) else exchange_status.lower(),
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
            status=exchange_status,
            filled_quantity=filled_quantity,
            filled_price=fill_price,
            exchange_order_id=exchange_result.get("exchange_order_id") or exchange_result.get("order_id"),
            fees=fill_fees,
        ) if fill_recorded else None,
        portfolio=PortfolioSnapshot.model_validate(portfolio) if portfolio is not None else None,
        statistics=StatisticsSnapshot.model_validate(statistics) if statistics is not None else None,
    )
    order_repository.save(
        payload.user_id,
        response,
        detail={
            "stage": "exchange",
            "external_order_id": exchange_result.get("order_id"),
            "exchange_status": exchange_status,
            "circuit_state": exchange_result.get("circuit_state"),
            "fill_recorded": fill_recorded,
            "portfolio_recorded": portfolio is not None,
            "statistics_recorded": statistics is not None,
        },
        idempotency_key=payload.idempotency_key,
    )
    # D20: shadow ledger MUST run before publish so the recorder's pnl lands
    # in response.fill.pnl, which the NATS event carries to outcome_consumer.
    # Without this the MAB sees pnl=0 forever and never learns from outcomes.
    if (
        payload.shadow_mode
        and response.status in {"FILLED", "SIMULATED_FILLED", "PARTIALLY_FILLED"}
        and payload.strategy_id
        and response.fill is not None
    ):
        try:
            from shared.shadow import ShadowFill
            from shared.shadow.recorder import get_recorder
            recorder = get_recorder()
            entry = float(response.fill.filled_price or payload.price or 0.0)
            qty = float(response.fill.filled_quantity or payload.quantity or 0.0)
            sign = 1.0 if payload.side.upper() == "BUY" else -1.0
            mark = entry
            if response.portfolio is not None:
                try:
                    pos = next((p for p in (response.portfolio.positions or []) if getattr(p, "asset", None) == payload.asset), None)
                    if pos is not None and getattr(pos, "average_price", None):
                        mark = float(pos.average_price)
                except Exception:
                    mark = entry
            shadow_fill = ShadowFill(
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
            # record_fill runs the FIFO matcher against prior opposite-side
            # open legs and overwrites shadow_fill.pnl when the upstream
            # heuristic pnl is 0 (i.e., mark == entry).
            recorder.record_fill(shadow_fill)
            # Propagate realized pnl into the response so publish_order_filled's
            # event payload carries a real reward signal — closes the MAB loop.
            response.fill.pnl = float(shadow_fill.pnl or 0.0)
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

    if fill_recorded:
        if response.status == "PARTIALLY_FILLED":
            publisher.publish_order_partially_filled(payload, response)
        else:
            publisher.publish_order_filled(payload, response)

    # Create protective orders (stop-loss, take-profit, trailing stop) for filled orders
    if response.status in {"FILLED", "SIMULATED_FILLED", "PARTIALLY_FILLED"} and response.fill is not None:
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

    # Post-fill: feed realized return to signal-service drift monitor.
    # Non-blocking fire-and-forget — drift monitor is observational,
    # failure here must never block order flow.
    if response.status in {"FILLED", "SIMULATED_FILLED", "PARTIALLY_FILLED"} and response.fill is not None:
        _notify_drift_monitor(payload.asset, response, intended_price=payload.price)

    _record_order_metrics(response.status, payload.shadow_mode, _start, exchange=payload.exchange, asset=payload.asset)
    return response
