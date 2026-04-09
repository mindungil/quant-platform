from app.db.repository import exchange_repository
from app.models.exchange import ExchangeOrderRequest


def test_exchange_repository_handles_shadow_mode() -> None:
    result = exchange_repository.place(
        ExchangeOrderRequest(exchange="binance", asset="BTCUSDT", side="BUY", quantity=1.0, shadow_mode=True)
    )
    assert result.status == "SIMULATED_FILLED"
    assert result.mode == "shadow"
    assert result.exchange_payload_signature
