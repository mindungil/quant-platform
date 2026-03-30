import httpx

from app.models.agent import MemoryRecord, MemorySearchRequest, MemorySearchResponse


class MemoryClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def search(self, request: MemorySearchRequest) -> MemorySearchResponse:
        response = httpx.post(
            f"{self._base_url}/memory/search",
            json=request.model_dump(mode="json"),
            timeout=5.0,
        )
        response.raise_for_status()
        return MemorySearchResponse.model_validate(response.json())

    def record(self, record: MemoryRecord) -> MemoryRecord:
        response = httpx.post(
            f"{self._base_url}/memory/record",
            json=record.model_dump(mode="json"),
            timeout=5.0,
        )
        response.raise_for_status()
        return MemoryRecord.model_validate(response.json())
