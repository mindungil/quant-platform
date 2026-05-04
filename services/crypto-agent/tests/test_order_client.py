import asyncio

from app.services import order_client


class _StubResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "approved": True,
            "reason": "submitted",
            "order_id": "order-1",
            "shadow_mode": False,
        }


class _StubAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        self.last_call = {"url": url, **kwargs}
        return _StubResponse()


def test_submit_order_attaches_internal_admin_headers(monkeypatch) -> None:
    stub_client = _StubAsyncClient()
    monkeypatch.setattr(order_client.httpx, "AsyncClient", lambda *args, **kwargs: stub_client)

    result = asyncio.run(order_client.submit_order("BTCUSDT", "BUY", user_id="user-123"))

    assert result.order_id == "order-1"
    headers = stub_client.last_call["headers"]
    assert headers["X-Internal-Actor-User-ID"] == "user-123"
    assert headers["X-Internal-Admin-Timestamp"]
    assert headers["X-Internal-Admin-Signature"]
