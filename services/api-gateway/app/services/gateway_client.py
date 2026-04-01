from typing import Any

import httpx

from shared.request_context import current_request_headers


class GatewayClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return httpx.request(
            method,
            f"{self._base_url}{path}",
            headers={**current_request_headers(), **(headers or {})},
            params=params,
            json=json,
            timeout=5.0,
        )

    def get(self, path: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
        response = self.request("GET", path, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, *, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> Any:
        response = self.request("POST", path, headers=headers, json=json)
        response.raise_for_status()
        return response.json()

    def patch(self, path: str, *, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None) -> Any:
        response = self.request("PATCH", path, headers=headers, json=json)
        response.raise_for_status()
        return response.json()
