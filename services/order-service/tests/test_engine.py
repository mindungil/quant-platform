from app.core import engine
from app.models.order import ExecutionConfig, OrderRequest


class StubRiskClient:
    def approve(self, payload):
        return {"approved": True, "reason": "approved"}


class StubExchangeClient:
    def place(self, payload):
        return {
            "status": "FILLED",
            "order_id": "order-1",
            "exchange_order_id": "exchange-1",
            "filled_quantity": payload.quantity,
            "average_fill_price": payload.price,
        }


class StubCredentialClient:
    def get(self, user_id, exchange):
        return {"user_id": user_id, "exchange": exchange, "sandbox": True, "label": "primary"}


class StubPortfolioClient:
    def __init__(self):
        self.applied = 0

    def get_snapshot(self, user_id):
        return {"positions": {}, "average_entry_prices": {}}

    def apply_fill(self, payload, *, order_id, status, fill_quantity, fill_price, filled_notional=None):
        self.applied += 1
        return {"user_id": payload.user_id, "positions": {payload.asset: fill_quantity}}


class StubStatisticsClient:
    def record_trade(self, payload, *, order_status, order_id=None, pre_fill_portfolio=None, fill_quantity=None, fill_price=None):
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
    def __init__(self):
        self.partials = 0
        self.filled = 0

    def publish_risk_triggered(self, **kwargs):
        return None

    def publish_order_created(self, payload, order_id):
        return None

    def publish_order_partially_filled(self, payload, response):
        self.partials += 1

    def publish_order_filled(self, payload, response):
        self.filled += 1


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
    portfolio_client = StubPortfolioClient()
    monkeypatch.setattr(engine, "portfolio_client", portfolio_client)
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
    assert result.fill is not None
    assert result.fill.exchange_order_id == "exchange-1"
    assert portfolio_client.applied == 1


class StubPartialExchangeClient:
    def place(self, payload):
        return {
            "status": "PARTIALLY_FILLED",
            "order_id": "order-3",
            "exchange_order_id": "exchange-3",
            "filled_quantity": 0.4,
            "average_fill_price": 101.5,
        }


def test_process_order_applies_partial_fill_quantities(monkeypatch) -> None:
    repository = StubOrderRepository()
    portfolio_client = StubPortfolioClient()
    publisher = StubPublisher()
    monkeypatch.setattr(engine, "order_repository", repository)
    monkeypatch.setattr(engine, "risk_client", StubRiskClient())
    monkeypatch.setattr(engine, "exchange_client", StubPartialExchangeClient())
    monkeypatch.setattr(engine, "credential_client", StubCredentialClient())
    monkeypatch.setattr(engine, "portfolio_client", portfolio_client)
    monkeypatch.setattr(engine, "statistics_client", StubStatisticsClient())
    monkeypatch.setattr(engine, "publisher", publisher)
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
    assert result.status == "PARTIALLY_FILLED"
    assert result.fill is not None
    assert result.fill.filled_quantity == 0.4
    assert result.fill.filled_price == 101.5
    assert portfolio_client.applied == 1
    assert publisher.partials == 1
    assert publisher.filled == 0


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


class StubRejectedExchangeClient:
    def place(self, payload):
        return {"status": "REJECTED_EXCHANGE_ERROR", "order_id": "order-2"}


def test_process_order_does_not_apply_portfolio_on_rejected_exchange(monkeypatch) -> None:
    repository = StubOrderRepository(
        ExecutionConfig(
            live_trading_enabled=False,
            allowed_exchanges=["binance"],
            default_shadow_mode=True,
            strict_runtime=False,
        )
    )
    portfolio_client = StubPortfolioClient()
    monkeypatch.setattr(engine, "order_repository", repository)
    monkeypatch.setattr(engine, "risk_client", StubRiskClient())
    monkeypatch.setattr(engine, "exchange_client", StubRejectedExchangeClient())
    monkeypatch.setattr(engine, "credential_client", StubCredentialClient())
    monkeypatch.setattr(engine, "portfolio_client", portfolio_client)
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
    assert result.status == "REJECTED_EXCHANGE_ERROR"
    assert result.fill is None
    assert result.portfolio is None
    assert portfolio_client.applied == 0
