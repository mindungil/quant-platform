from datetime import datetime, timezone

from app.core import recovery
from app.models.order import CredentialSnapshot, FillSnapshot, OrderResponse

UTC = timezone.utc


class StubOrderRepository:
    def __init__(self, order):
        self.order = order
        self.saved = []

    def save(self, user_id, response, *, detail=None, idempotency_key=None):
        self.saved.append((user_id, response, detail))

    def find_active_non_filled_orders(self):
        return [self.order]


class StubExchangeClient:
    def get_status(self, order_id):
        return {"order_id": order_id, "status": "FILLED"}

    def get_fills(self, order_id):
        return [{
            "order_id": order_id,
            "exchange_order_id": "exchange-123",
            "filled_quantity": 1.0,
            "average_fill_price": 101.0,
            "fees": 0.15,
            "fill_status": "FILLED",
            "status": "FILLED",
        }]


class StubPortfolioClient:
    def __init__(self):
        self.calls = []

    def get_snapshot(self, user_id):
        return {"positions": {"BTCUSDT": 0.4}, "average_entry_prices": {"BTCUSDT": 100.0}}

    def apply_fill(self, payload, *, order_id, status, fill_quantity, fill_price, filled_notional=None):
        self.calls.append((order_id, status, fill_quantity, fill_price))
        return {
            "user_id": payload.user_id,
            "positions": {"BTCUSDT": 1.0},
            "average_entry_prices": {"BTCUSDT": 100.6},
            "total_exposure": 101.0,
            "rebalance_needed": False,
        }


class StubStatisticsClient:
    def __init__(self):
        self.calls = []

    def record_trade(self, payload, *, order_status, order_id=None, pre_fill_portfolio=None, fill_quantity=None, fill_price=None):
        self.calls.append((order_status, order_id, fill_quantity, fill_price))
        return {
            "user_id": payload.user_id,
            "trade_count": 2,
            "total_return": 0.12,
            "win_rate": 1.0,
            "drift_detected": False,
        }


class StubPublisher:
    def __init__(self):
        self.published = []
        self.partials = []

    def publish_order_filled(self, payload, response):
        self.published.append((payload, response))

    def publish_order_partially_filled(self, payload, response):
        self.partials.append((payload, response))


def test_reconcile_order_applies_only_fill_delta(monkeypatch):
    order = OrderResponse(
        user_id="u1",
        order_id="order-1",
        asset="BTCUSDT",
        side="BUY",
        quantity=1.0,
        status="PARTIALLY_FILLED",
        risk_reason="approved",
        exchange="binance",
        shadow_mode=False,
        credential=CredentialSnapshot(user_id="u1", exchange="binance", loaded=True),
        fill=FillSnapshot(
            order_id="order-1",
            status="PARTIALLY_FILLED",
            filled_quantity=0.4,
            filled_price=100.0,
            exchange_order_id="exchange-123",
            fees=0.05,
        ),
        lifecycle=[
            {
                "status": "PENDING",
                "detail": {
                    "stage": "received",
                    "strategy_id": "s1",
                    "agent_name": "crypto-agent",
                    "lane": "core",
                },
                "created_at": datetime.now(UTC),
            }
        ],
    )
    repository = StubOrderRepository(order)
    portfolio_client = StubPortfolioClient()
    statistics_client = StubStatisticsClient()
    publisher = StubPublisher()

    monkeypatch.setattr(recovery, "order_repository", repository)
    monkeypatch.setattr(recovery, "exchange_client", StubExchangeClient())
    monkeypatch.setattr(recovery, "portfolio_client", portfolio_client)
    monkeypatch.setattr(recovery, "statistics_client", statistics_client)
    monkeypatch.setattr(recovery, "publisher", publisher)

    assert recovery.reconcile_order(order)
    assert portfolio_client.calls == [("order-1", "FILLED", 0.6, 101.0)]
    assert statistics_client.calls == [("FILLED", "order-1", 0.6, 101.0)]
    assert len(repository.saved) == 1
    saved_response = repository.saved[0][1]
    assert saved_response.status == "FILLED"
    assert saved_response.fill is not None
    assert saved_response.fill.filled_quantity == 1.0
    assert len(publisher.published) == 1


def test_reconcile_order_noop_when_exchange_state_unchanged(monkeypatch):
    order = OrderResponse(
        user_id="u1",
        order_id="order-2",
        asset="BTCUSDT",
        side="BUY",
        quantity=1.0,
        status="ACCEPTED",
        risk_reason="approved",
        exchange="binance",
        shadow_mode=False,
        credential=CredentialSnapshot(user_id="u1", exchange="binance", loaded=True),
    )

    class NoopExchangeClient:
        def get_status(self, order_id):
            return {"order_id": order_id, "status": "ACCEPTED"}

        def get_fills(self, order_id):
            return []

    repository = StubOrderRepository(order)
    monkeypatch.setattr(recovery, "order_repository", repository)
    monkeypatch.setattr(recovery, "exchange_client", NoopExchangeClient())
    monkeypatch.setattr(recovery, "portfolio_client", StubPortfolioClient())
    monkeypatch.setattr(recovery, "statistics_client", StubStatisticsClient())
    monkeypatch.setattr(recovery, "publisher", StubPublisher())

    assert not recovery.reconcile_order(order)
    assert repository.saved == []


def test_reconcile_order_emits_partial_event(monkeypatch):
    order = OrderResponse(
        user_id="u1",
        order_id="order-3",
        asset="BTCUSDT",
        side="BUY",
        quantity=1.0,
        status="ACCEPTED",
        risk_reason="approved",
        exchange="binance",
        shadow_mode=False,
        credential=CredentialSnapshot(user_id="u1", exchange="binance", loaded=True),
    )

    class PartialExchangeClient:
        def get_status(self, order_id):
            return {"order_id": order_id, "status": "PARTIALLY_FILLED"}

        def get_fills(self, order_id):
            return [{
                "order_id": order_id,
                "exchange_order_id": "exchange-xyz",
                "filled_quantity": 0.25,
                "average_fill_price": 99.5,
                "fees": 0.02,
                "fill_status": "PARTIALLY_FILLED",
                "status": "PARTIALLY_FILLED",
            }]

    repository = StubOrderRepository(order)
    portfolio_client = StubPortfolioClient()
    statistics_client = StubStatisticsClient()
    publisher = StubPublisher()

    monkeypatch.setattr(recovery, "order_repository", repository)
    monkeypatch.setattr(recovery, "exchange_client", PartialExchangeClient())
    monkeypatch.setattr(recovery, "portfolio_client", portfolio_client)
    monkeypatch.setattr(recovery, "statistics_client", statistics_client)
    monkeypatch.setattr(recovery, "publisher", publisher)

    assert recovery.reconcile_order(order)
    assert len(publisher.partials) == 1
    assert len(publisher.published) == 0
