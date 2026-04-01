from uuid import uuid4

from app.core.config import settings
from app.db.repository import order_repository
from app.models.order import CredentialSnapshot, FillSnapshot, OrderRequest, OrderResponse, PortfolioSnapshot, StatisticsSnapshot
from app.services.exchange_client import ExchangeClient
from app.services.risk_client import RiskClient
from app.services.credential_client import CredentialClient
from app.services.event_publisher import publisher
from app.services.portfolio_client import PortfolioClient
from app.services.statistics_client import StatisticsClient
from shared.logging import get_logger

risk_client = RiskClient(settings.risk_service_base_url)
exchange_client = ExchangeClient(settings.exchange_adapter_base_url)
credential_client = CredentialClient(settings.credential_store_base_url)
portfolio_client = PortfolioClient(settings.portfolio_service_base_url)
statistics_client = StatisticsClient(settings.statistics_service_base_url)
logger = get_logger("order-service")


def _record_lifecycle(order_id: str, user_id: str, status: str, detail: dict) -> None:
    if hasattr(order_repository, "record_lifecycle"):
        order_repository.record_lifecycle(order_id, user_id, status, detail=detail)


def process_order(payload: OrderRequest) -> OrderResponse:
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
        order_repository.save(payload.user_id, response, detail={"stage": "gate", "reason": "strategy_not_active"})
        publisher.publish_risk_triggered(
            payload=payload,
            reason="strategy_not_active",
            level="REJECTED",
            requested_notional=payload.requested_notional,
        )
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
        order_repository.save(payload.user_id, response, detail={"stage": "risk", "approval": approval})
        publisher.publish_risk_triggered(
            payload=payload,
            reason=approval["reason"],
            level=approval.get("level", "REJECTED"),
            requested_notional=payload.requested_notional,
        )
        return response
    _record_lifecycle(local_order_id, payload.user_id, "APPROVED", {"stage": "risk", "approval": approval})

    credential = credential_client.get(payload.user_id, payload.exchange)
    if credential is None:
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
        order_repository.save(payload.user_id, response, detail={"stage": "credential", "reason": "missing_credentials"})
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
            order_repository.save(payload.user_id, response, detail={"stage": "gate", "reason": "live_trading_disabled"})
            publisher.publish_risk_triggered(
                payload=payload,
                reason="live_trading_disabled",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
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
            order_repository.save(payload.user_id, response, detail={"stage": "gate", "reason": "exchange_not_allowed"})
            publisher.publish_risk_triggered(
                payload=payload,
                reason="exchange_not_allowed",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
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
            )
            publisher.publish_risk_triggered(
                payload=payload,
                reason="sandbox_credentials_for_live_order",
                level="REJECTED",
                requested_notional=payload.requested_notional,
            )
            return response

    payload.api_key = credential.get("api_key")
    payload.api_secret = credential.get("api_secret")
    payload.credential_label = credential.get("label")
    payload.credential_sandbox = credential.get("sandbox", True)
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
    )
    publisher.publish_order_filled(payload, response)
    return response
