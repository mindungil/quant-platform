from app.core.config import settings
from app.models.order import CredentialSnapshot, OrderRequest, OrderResponse
from app.services.exchange_client import ExchangeClient
from app.services.risk_client import RiskClient
from app.services.credential_client import CredentialClient

risk_client = RiskClient(settings.risk_service_base_url)
exchange_client = ExchangeClient(settings.exchange_adapter_base_url)
credential_client = CredentialClient(settings.credential_store_base_url)


def process_order(payload: OrderRequest) -> OrderResponse:
    approval = risk_client.approve(payload)
    if not approval["approved"]:
        return OrderResponse(
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status="REJECTED",
            risk_reason=approval["reason"],
            exchange="",
            shadow_mode=payload.shadow_mode,
            credential=CredentialSnapshot(user_id=payload.user_id, exchange=payload.exchange, loaded=False),
        )

    credential = credential_client.get(payload.user_id, payload.exchange)
    exchange_result = exchange_client.place(payload)
    return OrderResponse(
        asset=payload.asset,
        side=payload.side,
        quantity=payload.quantity,
        status=exchange_result["status"],
        risk_reason=approval["reason"],
        exchange=payload.exchange,
        shadow_mode=payload.shadow_mode,
        credential=CredentialSnapshot(user_id=payload.user_id, exchange=payload.exchange, loaded=credential is not None),
    )
