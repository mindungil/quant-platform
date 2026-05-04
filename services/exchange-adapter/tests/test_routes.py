from fastapi.testclient import TestClient

from shared.internal_admin import build_internal_admin_headers


class _RepoStub:
    def verify_credential(self, user_id: str, exchange: str) -> dict:
        return {"ok": True, "user_id": user_id, "exchange": exchange}

    def get_order_status(self, order_id: str) -> dict:
        return {"order_id": order_id, "status": "FILLED"}

    def get_order_fills(self, order_id: str) -> list[dict]:
        return [{"order_id": order_id, "filled_quantity": 1.0, "average_fill_price": 100.0}]


def test_verify_credential_requires_owner_or_internal(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")
    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "exchange_repository", _RepoStub())
    from app.main import app

    client = TestClient(app)
    response = client.post("/exchange/credentials/user-1/binance/verify")
    assert response.status_code == 401


def test_order_status_allows_signed_internal(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")
    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "exchange_repository", _RepoStub())
    from app.main import app

    client = TestClient(app)
    headers = build_internal_admin_headers("test-secret", "order-service", "/exchange/orders/order-1/status")
    response = client.get("/exchange/orders/order-1/status", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "FILLED"
