import httpx
from app.core.config import settings
from shared.internal_admin import build_internal_admin_headers
from shared.request_context import current_request_headers


class CredentialClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get(self, user_id: str, exchange: str) -> dict | None:
        response = httpx.get(
            f"{self._base_url}/credentials/{user_id}/{exchange}/reveal",
            headers={
                **current_request_headers(),
                **build_internal_admin_headers(
                    settings.internal_admin_secret,
                    user_id,
                    f"/credentials/{user_id}/{exchange}/reveal",
                ),
            },
            timeout=5.0,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
