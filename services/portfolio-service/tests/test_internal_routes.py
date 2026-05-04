from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from shared.internal_admin import build_internal_admin_headers


class _RepoStub:
    def get_summary(self) -> dict:
        return {"equity": 12345.0, "positions": {"BTCUSDT": 5000.0}, "kill_switch": False}

    def get_aggregate(self) -> dict:
        return {"total_exposure": 5000.0, "largest_position": "BTCUSDT"}


def _client(monkeypatch) -> TestClient:
    import app.api.routes as routes_module

    monkeypatch.setattr(routes_module, "portfolio_repository", _RepoStub())
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_summary_requires_signed_internal_headers(monkeypatch) -> None:
    response = _client(monkeypatch).get("/portfolio/summary")
    assert response.status_code == 403


def test_summary_allows_signed_internal_headers(monkeypatch) -> None:
    client = _client(monkeypatch)
    import app.api.routes as routes_module

    response = client.get(
        "/portfolio/summary",
        headers=build_internal_admin_headers(
            routes_module.settings.internal_admin_secret,
            "signal-service",
            "/portfolio/summary",
        ),
    )
    assert response.status_code == 200
    assert response.json()["equity"] == 12345.0


def test_aggregate_allows_signed_internal_headers(monkeypatch) -> None:
    client = _client(monkeypatch)
    import app.api.routes as routes_module

    response = client.get(
        "/portfolio/aggregate",
        headers=build_internal_admin_headers(
            routes_module.settings.internal_admin_secret,
            "orchestrator",
            "/portfolio/aggregate",
        ),
    )
    assert response.status_code == 200
    assert response.json()["largest_position"] == "BTCUSDT"
