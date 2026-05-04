from app.db.repository import exchange_repository
from app.models.exchange import ExchangeOrderRequest


def test_exchange_repository_handles_shadow_mode() -> None:
    result = exchange_repository.place(
        ExchangeOrderRequest(exchange="binance", asset="BTCUSDT", side="BUY", quantity=1.0, shadow_mode=True)
    )
    assert result.status == "SIMULATED_FILLED"
    assert result.mode == "shadow"
    assert result.filled_quantity == 1.0
    assert result.exchange_payload_signature


def test_exchange_repository_can_lookup_order_status() -> None:
    result = exchange_repository.place(
        ExchangeOrderRequest(
            user_id="status-user",
            exchange="binance",
            asset="BTCUSDT",
            side="BUY",
            quantity=1.0,
            shadow_mode=True,
            correlation_id="corr-1",
        )
    )
    status = exchange_repository.get_order_status("corr-1")
    assert status is not None
    assert status["status"] == result.status


def test_exchange_repository_can_lookup_order_fills() -> None:
    result = exchange_repository.place(
        ExchangeOrderRequest(
            user_id="fills-user",
            exchange="binance",
            asset="BTCUSDT",
            side="BUY",
            quantity=1.0,
            requested_notional=100.0,
            shadow_mode=True,
            correlation_id="corr-fills",
        )
    )
    fills = exchange_repository.get_order_fills("corr-fills")
    assert fills
    assert fills[0]["filled_quantity"] == result.filled_quantity
