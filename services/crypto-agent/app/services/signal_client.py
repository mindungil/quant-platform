import time

import httpx
from prometheus_client import Counter

from app.models.agent import SignalSnapshot
from shared.logging import get_logger
from shared.request_context import current_request_headers

logger = get_logger("signal-client")

signal_client_requests_total = Counter(
    "signal_client_requests_total",
    "Total signal client requests",
    ["status"],
)

_MAX_RETRIES = 3
_BACKOFF_SCHEDULE = [0.5, 1.0, 2.0]


class SignalClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_latest_signal(self, asset: str, *, user_id: str | None = None) -> SignalSnapshot:
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = httpx.get(
                    f"{self._base_url}/signals/{asset}/latest",
                    headers=headers,
                    timeout=5.0,
                )

                # Handle 429 rate limiting
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    logger.warning("signal_client_rate_limited", extra={
                        "asset": asset, "retry_after": retry_after, "attempt": attempt + 1,
                    })
                    signal_client_requests_total.labels(status="retry").inc()
                    time.sleep(retry_after)
                    continue

                if response.status_code == 404:
                    # No cached signal — trigger evaluation first
                    try:
                        eval_resp = httpx.post(
                            f"{self._base_url}/signals/{asset}/evaluate",
                            headers=headers,
                            timeout=10.0,
                        )
                        if eval_resp.status_code == 200:
                            signal_client_requests_total.labels(status="success").inc()
                            return SignalSnapshot.model_validate(eval_resp.json())
                    except Exception:
                        pass
                    # Fallback: return a neutral signal
                    signal_client_requests_total.labels(status="fallback").inc()
                    logger.warning("signal_fetch_failed_all_retries", extra={
                        "asset": asset, "reason": "404_after_evaluate_fallback",
                    })
                    return self._neutral_signal(asset)

                response.raise_for_status()
                signal_client_requests_total.labels(status="success").inc()
                return SignalSnapshot.model_validate(response.json())

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning("signal_client_http_error", extra={
                    "asset": asset, "status_code": exc.response.status_code,
                    "attempt": attempt + 1,
                })
                signal_client_requests_total.labels(status="retry").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SCHEDULE[attempt])

            except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
                last_exc = exc
                logger.warning("signal_client_connection_error", extra={
                    "asset": asset, "error": str(exc)[:100], "attempt": attempt + 1,
                })
                signal_client_requests_total.labels(status="retry").inc()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_BACKOFF_SCHEDULE[attempt])

        # All retries exhausted
        logger.error("signal_fetch_failed_all_retries", extra={
            "asset": asset, "error": str(last_exc)[:200], "attempts": _MAX_RETRIES,
        })
        signal_client_requests_total.labels(status="fallback").inc()
        return self._neutral_signal(asset)

    @staticmethod
    def _neutral_signal(asset: str) -> SignalSnapshot:
        from datetime import UTC, datetime
        return SignalSnapshot(
            asset=asset,
            timestamp=datetime.now(UTC),
            signal_score=0.0,
            threshold=0.6,
            threshold_crossed=False,
            direction="HOLD",
            components={},
            feature_timestamp=datetime.now(UTC),
        )
