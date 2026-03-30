from app.core import engine
from app.models.order import OrderRequest


class StubRiskClient:
    def approve(self, payload):
        return {"approved": True, "reason": "approved"}


class StubExchangeClient:
    def place(self, payload):
        return {"status": "FILLED"}


class StubCredentialClient:
    def get(self, user_id, exchange):
        return {"user_id": user_id, "exchange": exchange}


def test_process_order_returns_filled(monkeypatch) -> None:
    monkeypatch.setattr(engine, "risk_client", StubRiskClient())
    monkeypatch.setattr(engine, "exchange_client", StubExchangeClient())
    monkeypatch.setattr(engine, "credential_client", StubCredentialClient())
    result = engine.process_order(
        OrderRequest(
            user_id="u1",
            exchange="binance",
            asset="BTCUSDT",
            side="BUY",
            quantity=1,
            requested_notional=100,
            max_notional=1000,
            current_drawdown=0.01,
        )
    )
    assert result.status == "FILLED"
