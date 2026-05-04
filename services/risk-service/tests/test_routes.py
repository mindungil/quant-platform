from fastapi.testclient import TestClient

from shared.internal_admin import build_internal_admin_headers


class _RiskRepoStub:
    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[dict]:
        return [{
            "user_id": user_id,
            "asset": "BTCUSDT",
            "level": "WARN",
            "approved": False,
            "reason": "limit",
            "requested_notional": 100.0,
            "exposure_ratio": 0.2,
            "payload": {},
            "created_at": "2026-04-22T00:00:00Z",
        }]


def test_risk_settings_requires_user_context(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")
    from app.main import app

    client = TestClient(app)
    response = client.get("/risk/settings/user-1")
    assert response.status_code == 401


def test_risk_incidents_allow_signed_internal(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")
    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "risk_repository", _RiskRepoStub())
    from app.main import app

    client = TestClient(app)
    headers = build_internal_admin_headers("test-secret", "api-gateway", "/risk/incidents/user-1")
    response = client.get("/risk/incidents/user-1", headers=headers)
    assert response.status_code == 200


def test_recent_risk_incidents_require_signed_internal(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_SECRET", "test-secret")
    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "risk_repository", _RiskRepoStub())
    from app.main import app

    client = TestClient(app)
    response = client.get("/risk/incidents/recent")
    assert response.status_code == 401

    headers = build_internal_admin_headers("test-secret", "api-gateway", "/risk/incidents/recent")
    signed = client.get("/risk/incidents/recent", headers=headers)
    assert signed.status_code == 200
    assert signed.json()[0]["asset"] == "BTCUSDT"
