from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from shared.internal_admin import build_internal_admin_headers


class _IssueResponse:
    access_token = "token"
    refresh_token = "refresh"
    token_type = "bearer"
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    claims = {
        "sub": "user-1",
        "email": "user@example.com",
        "roles": ["user"],
        "plan": "pro",
        "iat": 1,
        "exp": 9999999999,
        "iss": "quant-auth-service",
    }

    def model_dump(self, mode: str = "python") -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "claims": self.claims,
        }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_auth_token_rejects_unsigned_internal_request() -> None:
    response = _client().post(
        "/auth/token",
        json={"user_id": "user-1", "email": "user@example.com", "roles": ["user"], "plan": "pro"},
        headers={"X-Internal-Actor-User-ID": "user-1"},
    )
    assert response.status_code == 403


def test_auth_token_accepts_signed_internal_request(monkeypatch) -> None:
    import app.api.routes as routes_module
    monkeypatch.setattr(routes_module, "issue_access_token", lambda payload: _IssueResponse())

    client = _client()
    headers = build_internal_admin_headers(
        routes_module.settings.internal_admin_secret,
        "user-1",
        "/auth/token",
    )
    response = client.post(
        "/auth/token",
        json={"user_id": "user-1", "email": "user@example.com", "roles": ["user"], "plan": "pro"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "token"
