import time

import httpx
from prometheus_client import Counter

from app.models.agent import MemoryRecord, MemorySearchRequest, MemorySearchResponse
from shared.logging import get_logger
from shared.request_context import current_request_headers

logger = get_logger("memory-client")

memory_client_requests_total = Counter(
    "memory_client_requests_total",
    "Total memory client requests",
    ["method", "status"],
)

_MAX_RETRIES = 3
_BACKOFF_SECONDS = 0.5


class MemoryClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def search(self, request: MemorySearchRequest) -> MemorySearchResponse:
        headers = {**current_request_headers(), **({"X-User-ID": request.user_id} if request.user_id else {})}
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = httpx.post(
                    f"{self._base_url}/memory/search",
                    json=request.model_dump(mode="json", exclude_none=True),
                    headers=headers,
                    timeout=5.0,
                )
                response.raise_for_status()
                memory_client_requests_total.labels(method="search", status="success").inc()
                return MemorySearchResponse.model_validate(response.json())

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning("memory_search_http_error", extra={
                    "status_code": exc.response.status_code,
                    "attempt": attempt + 1,
                })
                memory_client_requests_total.labels(method="search", status="error").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)

            except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                logger.warning("memory_search_connection_error", extra={
                    "error": str(exc)[:100],
                    "attempt": attempt + 1,
                })
                memory_client_requests_total.labels(method="search", status="error").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)

        logger.error("memory_search_failed_all_retries", extra={
            "error": str(last_exc)[:200], "attempts": _MAX_RETRIES,
        })
        # Return empty response so decisions are not blocked
        return MemorySearchResponse(query=request, items=[])

    def record(self, record: MemoryRecord) -> MemoryRecord:
        headers = {**current_request_headers(), **({"X-User-ID": record.user_id} if record.user_id else {})}
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = httpx.post(
                    f"{self._base_url}/memory/record",
                    json=record.model_dump(mode="json", exclude_none=True),
                    headers=headers,
                    timeout=5.0,
                )
                response.raise_for_status()
                memory_client_requests_total.labels(method="record", status="success").inc()
                return MemoryRecord.model_validate(response.json())

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning("memory_record_http_error", extra={
                    "status_code": exc.response.status_code,
                    "attempt": attempt + 1,
                })
                memory_client_requests_total.labels(method="record", status="error").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)

            except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                logger.warning("memory_record_connection_error", extra={
                    "error": str(exc)[:100],
                    "attempt": attempt + 1,
                })
                memory_client_requests_total.labels(method="record", status="error").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)

        # Record failures should never block decisions — return stub
        logger.error("memory_record_failed_all_retries", extra={
            "error": str(last_exc)[:200], "attempts": _MAX_RETRIES,
            "asset": record.asset,
        })
        return record

    def reinforce(self, memory_id: str, trade_outcome: float, outcome_sharpe: float = 0.0) -> dict:
        """Reinforce a memory record with trade outcome."""
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = httpx.post(
                    f"{self._base_url}/memory/{memory_id}/reinforce",
                    json={"trade_outcome": trade_outcome, "outcome_sharpe": outcome_sharpe},
                    timeout=5.0,
                )
                response.raise_for_status()
                memory_client_requests_total.labels(method="reinforce", status="success").inc()
                return response.json()

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning("memory_reinforce_http_error", extra={
                    "memory_id": memory_id,
                    "status_code": exc.response.status_code,
                    "attempt": attempt + 1,
                })
                memory_client_requests_total.labels(method="reinforce", status="error").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)

            except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                logger.warning("memory_reinforce_connection_error", extra={
                    "memory_id": memory_id,
                    "error": str(exc)[:100],
                    "attempt": attempt + 1,
                })
                memory_client_requests_total.labels(method="reinforce", status="error").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SECONDS)

        logger.error("memory_reinforce_failed_all_retries", extra={
            "memory_id": memory_id,
            "error": str(last_exc)[:200],
            "attempts": _MAX_RETRIES,
        })
        raise last_exc  # type: ignore[misc]
