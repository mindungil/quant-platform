from app.services.statistics_client import StatisticsClient, _compute_realized_pnl
from app.models.order import OrderRequest


def _payload(*, side: str, quantity: float, price: float) -> OrderRequest:
    return OrderRequest(
        user_id="user-1",
        exchange="binance",
        asset="BTCUSDT",
        side=side,
        quantity=quantity,
        price=price,
        requested_notional=quantity * price,
        max_notional=10000,
        current_drawdown=0.0,
    )


def test_compute_realized_pnl_for_long_close() -> None:
    pnl = _compute_realized_pnl(
        _payload(side="SELL", quantity=1.5, price=110.0),
        {
            "positions": {"BTCUSDT": 2.0},
            "average_entry_prices": {"BTCUSDT": 100.0},
        },
        fill_quantity=1.5,
        fill_price=110.0,
    )
    assert pnl == 15.0


def test_compute_realized_pnl_for_short_cover() -> None:
    pnl = _compute_realized_pnl(
        _payload(side="BUY", quantity=2.0, price=90.0),
        {
            "positions": {"BTCUSDT": -3.0},
            "average_entry_prices": {"BTCUSDT": 100.0},
        },
        fill_quantity=2.0,
        fill_price=90.0,
    )
    assert pnl == 20.0


def test_compute_realized_pnl_for_new_position_is_zero() -> None:
    pnl = _compute_realized_pnl(
        _payload(side="BUY", quantity=1.0, price=100.0),
        {
            "positions": {"BTCUSDT": 0.0},
            "average_entry_prices": {"BTCUSDT": 0.0},
        },
        fill_quantity=1.0,
        fill_price=100.0,
    )
    assert pnl == 0.0


class _StubResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"ok": True}


def test_record_trade_uses_internal_headers_and_metadata(monkeypatch) -> None:
    captured: dict = {}

    def _fake_post(url: str, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return _StubResponse()

    import app.services.statistics_client as module
    monkeypatch.setattr(module.httpx, "post", _fake_post)

    payload = OrderRequest(
        user_id="user-1",
        exchange="binance",
        asset="BTCUSDT",
        side="SELL",
        quantity=1.0,
        price=110.0,
        requested_notional=110.0,
        max_notional=10000,
        current_drawdown=0.0,
        strategy_id="strategy-1",
        agent_name="crypto-agent",
        lane="agent_core",
    )
    result = StatisticsClient("http://statistics-service").record_trade(
        payload,
        order_status="FILLED",
        order_id="order-1",
        pre_fill_portfolio={"positions": {"BTCUSDT": 1.0}, "average_entry_prices": {"BTCUSDT": 100.0}},
        fill_quantity=0.5,
        fill_price=111.0,
    )

    assert result == {"ok": True}
    assert captured["url"].endswith("/statistics/record")
    assert captured["headers"]["X-Internal-Actor-User-ID"] == "user-1"
    assert captured["headers"]["X-Internal-Admin-Signature"]
    assert captured["json"]["strategy_id"] == "strategy-1"
    assert captured["json"]["agent_name"] == "crypto-agent"
    assert captured["json"]["lane"] == "agent_core"
    assert captured["json"]["quantity"] == 0.5
    assert captured["json"]["fill_price"] == 111.0
