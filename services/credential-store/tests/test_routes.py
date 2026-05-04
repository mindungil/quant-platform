from fastapi.testclient import TestClient

from shared.internal_admin import build_internal_admin_headers


class _RepoStub:
    def get(self, user_id: str, exchange: str):
        return {
            "user_id": user_id,
            "exchange": exchange,
            "label": "primary",
            "sandbox": False,
            "api_key": "key",
            "api_secret": "secret",
        }


def test_reveal_credential_allows_signed_internal_headers(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")

    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "credential_repository", _RepoStub())

    from app.main import app

    client = TestClient(app)
    headers = build_internal_admin_headers(
        "test-secret",
        "user-1",
        "/credentials/user-1/binance/reveal",
    )
    response = client.get("/credentials/user-1/binance/reveal", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "user-1"
    assert body["exchange"] == "binance"


def test_reveal_credential_rejects_legacy_internal_secret(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")

    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "credential_repository", _RepoStub())

    from app.main import app

    client = TestClient(app)
    response = client.get(
        "/credentials/user-1/binance/reveal",
        headers={"X-Internal-Secret": "test-secret"},
    )

    assert response.status_code == 401
