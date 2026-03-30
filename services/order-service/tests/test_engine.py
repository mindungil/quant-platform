from app.core import engine
from app.models.order import OrderRequest


class StubRiskClient:
    def approve(self, payload):
        return {"approved": True, "reason": "approved"}


class StubExchangeClient:
    def place(self, payload):
        return {"status": "FILLED", "order_id": "order-1"}


class StubCredentialClient:
    def get(self, user_id, exchange):
        return {"user_id": user_id, "exchange": exchange, "sandbox": True, "label": "primary"}


class StubPortfolioClient:
    def apply_fill(self, payload, *, order_id, status):
        return {"user_id": payload.user_id, "positions": {payload.asset: payload.quantity}}


class StubStatisticsClient:
    def record_trade(self, payload, *, order_status):
        return {"user_id": payload.user_id, "trade_count": 1, "total_return": -1.0, "win_rate": 0.0, "drift_detected": False}


def test_process_order_returns_filled(monkeypatch) -> None:
    monkeypatch.setattr(engine, "risk_client", StubRiskClient())
    monkeypatch.setattr(engine, "exchange_client", StubExchangeClient())
    monkeypatch.setattr(engine, "credential_client", StubCredentialClient())
    monkeypatch.setattr(engine, "portfolio_client", StubPortfolioClient())
    monkeypatch.setattr(engine, "statistics_client", StubStatisticsClient())
    result = engine.process_order(
        OrderRequest(
            user_id="u1",
            exchange="binance",
            asset="BTCUSDT",
            side="BUY",
            quantity=1,
            price=100,
            requested_notional=100,
            max_notional=1000,
            current_drawdown=0.01,
            current_exposure=0.0,
            exposure_limit=1000.0,
        )
    )
    assert result.status == "FILLED"
    assert result.order_id == "order-1"
    assert result.portfolio is not None
