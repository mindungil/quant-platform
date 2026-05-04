"""Multi-tenancy regression tests for order-service routes."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.models.order import CredentialSnapshot, OrderResponse


def _make_order(order_id: str, user_id: str, status: str = "PENDING"):
    return OrderResponse(
        user_id=user_id,
        order_id=order_id,
        asset="BTCUSDT",
        side="BUY",
        quantity=0.01,
        status=status,
        risk_reason="approved",
        exchange="binance",
        shadow_mode=True,
        credential=CredentialSnapshot(user_id=user_id, exchange="binance", loaded=True),
    )


@patch("app.core.engine.exchange_client")
@patch("app.db.repository.order_repository")
@patch("app.services.event_publisher.publisher")
@patch("app.services.nats_consumer.consumer")
@patch("app.services.position_monitor.start", return_value=None)
@patch("app.services.position_monitor.stop", return_value=None)
def _get_client(*_mocks):
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


_client = _get_client()


@patch("app.api.routes.order_repository")
def test_get_order_detail_forbidden(mock_repo):
    """User A cannot view User B's order."""
    mock_repo.get_by_id.return_value = _make_order("ord-1", "user-B")
    resp = _client.get("/orders/detail/ord-1", headers={"x-user-id": "user-A"})
    assert resp.status_code == 403


@patch("app.api.routes.order_repository")
def test_cancel_order_forbidden(mock_repo):
    """User A cannot cancel User B's order."""
    mock_repo.get_by_id.return_value = _make_order("ord-1", "user-B")
    resp = _client.delete("/orders/ord-1", headers={"x-user-id": "user-A"})
    assert resp.status_code == 403


@patch("app.api.routes.order_repository")
def test_get_order_detail_own(mock_repo):
    """Owner can view their own order."""
    order = _make_order("ord-1", "user-A")
    mock_repo.get_by_id.return_value = order
    resp = _client.get("/orders/detail/ord-1", headers={"x-user-id": "user-A"})
    assert resp.status_code == 200
