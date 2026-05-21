import logging
from datetime import datetime, timezone

import httpx

from app.models.signal import ExternalContextSnapshot
from shared.request_context import current_request_headers

_logger = logging.getLogger("signal-service.external-data-client")


def _degraded_snapshot(asset: str, reason: str) -> ExternalContextSnapshot:
    """Returned when external-data-service is unreachable or slow.

    Carries `degraded_mode=True` so downstream scoring weights the
    external signal at zero (build_signal_response checks this flag).
    """
    now = datetime.now(timezone.utc)
    return ExternalContextSnapshot(
        asset=asset,
        timestamp=now,
        source_timestamp=None,
        missing_fields=[reason],
        degraded_mode=True,
        stale=True,
        source="degraded",
    )


class ExternalDataClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_external_context(self, asset: str) -> ExternalContextSnapshot:
        # V8: was timeout=5.0 with raise_for_status() propagating any
        # failure up to /signals/evaluate, where it became a 500. Now
        # we return a degraded snapshot instead of raising — the route
        # can still produce a signal, just without the external boost.
        # external-data-service handles its own negative caching;
        # this client only needs to survive its slow window.
        try:
            response = httpx.get(
                f"{self._base_url}/external/context/{asset}",
                headers=current_request_headers(),
                timeout=3.0,
            )
            response.raise_for_status()
            return ExternalContextSnapshot.model_validate(response.json())
        except httpx.TimeoutException:
            _logger.warning("external_context_timeout", extra={"asset": asset})
            return _degraded_snapshot(asset, "timeout")
        except httpx.HTTPError as exc:
            _logger.warning("external_context_http_error",
                            extra={"asset": asset, "err": str(exc)[:120]})
            return _degraded_snapshot(asset, f"http_error:{type(exc).__name__}")
        except Exception as exc:
            _logger.warning("external_context_unexpected",
                            extra={"asset": asset, "err": str(exc)[:120]})
            return _degraded_snapshot(asset, f"unexpected:{type(exc).__name__}")
