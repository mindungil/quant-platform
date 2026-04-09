import httpx
from prometheus_client import Counter

from app.models.agent import StrategySnapshot
from shared.logging import get_logger
from shared.request_context import current_request_headers

logger = get_logger("strategy-client")

strategy_client_requests_total = Counter(
    "strategy_client_requests_total",
    "Total strategy client requests",
    ["status"],
)

_BOOTSTRAP_FALLBACK = StrategySnapshot(
    id="bootstrap_fallback",
    name="bootstrap_fallback",
    asset_type="crypto",
    indicators=[],
    weights={},
    thresholds={"entry": 0.6},
    version="0.0.0",
    status="ACTIVE",
)


class StrategyClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def get_active_strategy(self, asset_type: str, *, user_id: str | None = None) -> StrategySnapshot:
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}
        try:
            response = httpx.get(
                f"{self._base_url}/strategies/active",
                headers=headers,
                params={"asset_type": asset_type},
                timeout=5.0,
            )
            response.raise_for_status()
            strategy_client_requests_total.labels(status="success").inc()
            return StrategySnapshot.model_validate(response.json())

        except httpx.HTTPStatusError as exc:
            logger.warning("strategy_client_http_error", extra={
                "asset_type": asset_type,
                "status_code": exc.response.status_code,
                "user_id": user_id,
            })
            strategy_client_requests_total.labels(status="fallback").inc()
            return _BOOTSTRAP_FALLBACK

        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            logger.warning("strategy_client_connection_error", extra={
                "asset_type": asset_type,
                "error": str(exc)[:100],
                "user_id": user_id,
            })
            strategy_client_requests_total.labels(status="fallback").inc()
            return _BOOTSTRAP_FALLBACK

        except Exception as exc:
            logger.warning("strategy_client_unexpected_error", extra={
                "asset_type": asset_type,
                "error": str(exc)[:100],
                "user_id": user_id,
            })
            strategy_client_requests_total.labels(status="error").inc()
            return _BOOTSTRAP_FALLBACK
