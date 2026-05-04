from app.services.credential_client import CredentialClient


class _StubResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"api_key": "key", "api_secret": "secret"}


def test_credential_client_uses_internal_admin_headers(monkeypatch) -> None:
    captured: dict = {}

    def _fake_get(url: str, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _StubResponse()

    import app.services.credential_client as module
    monkeypatch.setattr(module.httpx, "get", _fake_get)

    client = CredentialClient("http://credential-store")
    result = client.get("user-1", "binance")

    assert result == {"api_key": "key", "api_secret": "secret"}
    assert captured["url"].endswith("/credentials/user-1/binance/reveal")
    assert captured["headers"]["X-Internal-Actor-User-ID"] == "user-1"
    assert captured["headers"]["X-Internal-Admin-Timestamp"]
    assert captured["headers"]["X-Internal-Admin-Signature"]
