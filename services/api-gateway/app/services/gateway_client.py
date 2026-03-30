from typing import Any

import httpx


class GatewayClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get(self, path: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
        response = httpx.get(f"{self._base_url}{path}", headers=headers, params=params, timeout=5.0)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, *, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> Any:
        response = httpx.post(f"{self._base_url}{path}", headers=headers, json=json, timeout=5.0)
        response.raise_for_status()
        return response.json()

    def patch(self, path: str, *, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> Any:
        response = httpx.patch(f"{self._base_url}{path}", headers=headers, json=json, timeout=5.0)
        response.raise_for_status()
        return response.json()
