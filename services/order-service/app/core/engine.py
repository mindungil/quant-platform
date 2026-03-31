from app.core.config import settings
from app.db.repository import order_repository
from app.models.order import CredentialSnapshot, FillSnapshot, OrderRequest, OrderResponse, PortfolioSnapshot, StatisticsSnapshot
from app.services.exchange_client import ExchangeClient
from app.services.risk_client import RiskClient
from app.services.credential_client import CredentialClient
from app.services.portfolio_client import PortfolioClient
from app.services.statistics_client import StatisticsClient
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus

risk_client = RiskClient(settings.risk_service_base_url)
exchange_client = ExchangeClient(settings.exchange_adapter_base_url)
credential_client = CredentialClient(settings.credential_store_base_url)
portfolio_client = PortfolioClient(settings.portfolio_service_base_url)
statistics_client = StatisticsClient(settings.statistics_service_base_url)
realtime_bus = RealtimeBus(RedisStore(settings.redis_url), replay_limit=settings.realtime_replay_limit)


def process_order(payload: OrderRequest) -> OrderResponse:
    approval = risk_client.approve(payload)
    if not approval["approved"]:
        response = OrderResponse(
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
        order_repository.save(payload.user_id, response)
        realtime_bus.publish(
            event_type="risk.triggered",
            source="order-service",
            user_id=payload.user_id,
            data={
                "asset": payload.asset,
                "exchange": payload.exchange,
                "level": "REJECTED",
                "reason": approval["reason"],
                "requested_notional": payload.requested_notional,
            },
        )
        return response

    credential = credential_client.get(payload.user_id, payload.exchange)
    exchange_result = exchange_client.place(payload)
    order_id = exchange_result.get("order_id")
    portfolio = portfolio_client.apply_fill(payload, order_id=order_id, status=exchange_result["status"])
    try:
        statistics = statistics_client.record_trade(payload, order_status=exchange_result["status"], order_id=order_id)
    except TypeError:
        statistics = statistics_client.record_trade(payload, order_status=exchange_result["status"])
    response = OrderResponse(
        user_id=payload.user_id,
        order_id=order_id,
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
            order_id=order_id,
            status=exchange_result["status"],
            filled_quantity=payload.quantity,
            filled_price=payload.price,
        ),
        portfolio=PortfolioSnapshot.model_validate(portfolio),
        statistics=StatisticsSnapshot.model_validate(statistics),
    )
    order_repository.save(payload.user_id, response)
    realtime_bus.publish(
        event_type="order.filled",
        source="order-service",
        user_id=payload.user_id,
        data=response.model_dump(mode="json"),
    )
    return response
