from app.core import engine
from app.models.order import ExecutionConfig, OrderRequest


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


class StubOrderRepository:
    def __init__(self, execution_config: ExecutionConfig | None = None):
        self.execution_config = execution_config or ExecutionConfig(
            live_trading_enabled=False,
            allowed_exchanges=["binance"],
            default_shadow_mode=True,
            strict_runtime=False,
        )
        self.saved = []

    def get_execution_config(self):
        return self.execution_config

    def save(self, user_id, response, *, detail=None, idempotency_key=None):
        self.saved.append((user_id, response, detail))

    def get_by_idempotency_key(self, key):
        return None

    def record_lifecycle(self, order_id, user_id, status, *, detail):
        self.saved.append((user_id, {"order_id": order_id, "status": status}, detail))


class StubPublisher:
    def publish_risk_triggered(self, **kwargs):
        return None

    def publish_order_created(self, payload, order_id):
        return None

    def publish_order_filled(self, payload, response):
        return None


def test_process_order_returns_filled(monkeypatch) -> None:
    repository = StubOrderRepository(
        ExecutionConfig(
            live_trading_enabled=False,
            allowed_exchanges=["binance"],
            default_shadow_mode=True,
            strict_runtime=False,
        )
    )
    monkeypatch.setattr(engine, "order_repository", repository)
    monkeypatch.setattr(engine, "risk_client", StubRiskClient())
    monkeypatch.setattr(engine, "exchange_client", StubExchangeClient())
    monkeypatch.setattr(engine, "credential_client", StubCredentialClient())
    monkeypatch.setattr(engine, "portfolio_client", StubPortfolioClient())
    monkeypatch.setattr(engine, "statistics_client", StubStatisticsClient())
    monkeypatch.setattr(engine, "publisher", StubPublisher())
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
    assert result.order_id
    assert result.portfolio is not None


def test_process_order_blocks_live_when_admin_toggle_disabled(monkeypatch) -> None:
    repository = StubOrderRepository(
        ExecutionConfig(
            live_trading_enabled=False,
            allowed_exchanges=["binance"],
            default_shadow_mode=False,
            strict_runtime=False,
        )
    )
    monkeypatch.setattr(engine, "order_repository", repository)
    monkeypatch.setattr(engine, "risk_client", StubRiskClient())
    monkeypatch.setattr(engine, "exchange_client", StubExchangeClient())
    monkeypatch.setattr(engine, "credential_client", StubCredentialClient())
    monkeypatch.setattr(engine, "portfolio_client", StubPortfolioClient())
    monkeypatch.setattr(engine, "statistics_client", StubStatisticsClient())
    monkeypatch.setattr(engine, "publisher", StubPublisher())
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
            shadow_mode=False,
        )
    )
    assert result.status == "REJECTED"
    assert result.risk_reason == "live_trading_disabled"
