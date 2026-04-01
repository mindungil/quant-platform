import httpx

from app.models.agent import MemoryRecord, MemorySearchRequest, MemorySearchResponse
from shared.request_context import current_request_headers


class MemoryClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def search(self, request: MemorySearchRequest) -> MemorySearchResponse:
        headers = {**current_request_headers(), **({"X-User-ID": request.user_id} if request.user_id else {})}
        response = httpx.post(
            f"{self._base_url}/memory/search",
            json=request.model_dump(mode="json", exclude_none=True),
            headers=headers,
            timeout=5.0,
        )
        response.raise_for_status()
        return MemorySearchResponse.model_validate(response.json())

    def record(self, record: MemoryRecord) -> MemoryRecord:
        headers = {**current_request_headers(), **({"X-User-ID": record.user_id} if record.user_id else {})}
        response = httpx.post(
            f"{self._base_url}/memory/record",
            json=record.model_dump(mode="json", exclude_none=True),
            headers=headers,
            timeout=5.0,
        )
        response.raise_for_status()
        return MemoryRecord.model_validate(response.json())
