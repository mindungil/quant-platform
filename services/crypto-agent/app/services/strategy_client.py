import httpx
from prometheus_client import Counter

from app.models.agent import StrategySnapshot
from shared.logging import get_logger
from shared.internal_admin import build_internal_admin_headers
from shared.request_context import current_request_headers
from app.core.config import settings

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

    def list_subscriptions(
        self,
        asset_type: str,
        *,
        user_id: str | None = None,
    ) -> list[dict]:
        """Fetch enabled template subscriptions for the current user.

        Returns a list of subscription dicts with template_id, weight, etc.
        Returns empty list on failure (template lane is opt-in).
        """
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}
        try:
            response = httpx.get(
                f"{self._base_url}/templates/subscriptions",
                headers=headers,
                params={"asset_type": asset_type, "status": "enabled"},
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("strategy_client_list_subs_error", extra={
                "error": str(exc)[:100],
                "user_id": user_id,
            })
            return []

    def list_all_enabled_subscriptions(self, asset_type: str) -> list[dict]:
        """Fetch every enabled subscription across all users (internal)."""
        try:
            response = httpx.get(
                f"{self._base_url}/templates/subscriptions/all",
                headers=build_internal_admin_headers(
                    settings.internal_admin_secret,
                    "crypto-agent",
                    "/templates/subscriptions/all",
                ),
                params={"asset_type": asset_type},
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("strategy_client_list_all_subs_error", extra={
                "error": str(exc)[:100],
            })
            return []

    def get_allocation(
        self,
        asset_type: str,
        *,
        user_id: str | None = None,
    ) -> dict:
        """Fetch lane allocation for the user. Returns {agent_pct, template_pct}.
        Falls back to defaults on failure."""
        headers = {**current_request_headers(), **({"X-User-ID": user_id} if user_id else {})}
        try:
            response = httpx.get(
                f"{self._base_url}/settings/lane-allocation",
                headers=headers,
                params={"asset_type": asset_type},
                timeout=5.0,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return {"agent_pct": 0.70, "template_pct": 0.30}

    def get_template(self, template_id: str) -> dict | None:
        """Fetch template definition by id."""
        try:
            response = httpx.get(
                f"{self._base_url}/strategies/templates/{template_id}",
                timeout=5.0,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning("strategy_client_get_template_error", extra={
                "template_id": template_id,
                "error": str(exc)[:100],
            })
            return None
