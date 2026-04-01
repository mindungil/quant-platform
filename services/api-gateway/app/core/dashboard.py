from datetime import UTC, datetime

from app.core.auth import build_internal_admin_headers
from app.core.config import settings
from app.models.auth import GatewayPrincipal
from app.services.gateway_client import GatewayClient

signal_client = GatewayClient(settings.signal_service_base_url)
order_client = GatewayClient(settings.order_service_base_url)
credential_client = GatewayClient(settings.credential_store_base_url)
portfolio_client = GatewayClient(settings.portfolio_service_base_url)
statistics_client = GatewayClient(settings.statistics_service_base_url)
memory_client = GatewayClient(settings.memory_service_base_url)
strategy_client = GatewayClient(settings.strategy_registry_base_url)


def build_dashboard_summary(principal: GatewayPrincipal) -> dict:
    headers = principal.forwarded_headers
    summary: dict[str, object] = {
        "user": principal.model_dump(),
        "generated_at": datetime.now(UTC).isoformat(),
        "services": {
            "signal_service": settings.signal_service_base_url,
            "portfolio_service": settings.portfolio_service_base_url,
            "statistics_service": settings.statistics_service_base_url,
        },
    }

    try:
        summary["active_strategy"] = strategy_client.get(
            "/strategies/active",
            headers=headers,
            params={"asset_type": "crypto"},
        )
    except Exception as exc:
        summary["active_strategy_error"] = str(exc)

    try:
        summary["signals"] = signal_client.get("/signals")
    except Exception as exc:
        summary["signals_error"] = str(exc)

    try:
        summary["memory_probe"] = memory_client.post(
            "/memory/search",
            headers=headers,
            json={
                "user_id": principal.user_id,
                "asset": "BTCUSDT",
                "asset_type": "crypto",
                "signal_score": 0.0,
                "action": "HOLD",
                "top_k": 3,
            },
        )
    except Exception as exc:
        summary["memory_probe_error"] = str(exc)

    try:
        summary["portfolio"] = portfolio_client.get(f"/portfolio/{principal.user_id}")
    except Exception as exc:
        summary["portfolio_error"] = str(exc)

    try:
        summary["statistics"] = statistics_client.get(f"/statistics/{principal.user_id}")
    except Exception as exc:
        summary["statistics_error"] = str(exc)

    try:
        summary["orders"] = order_client.get(f"/orders/{principal.user_id}")
    except Exception as exc:
        summary["orders_error"] = str(exc)

    try:
        credentials: list[dict] = []
        for exchange in ("binance", "upbit", "alpaca"):
            try:
                credentials.append(
                    credential_client.get(
                        f"/credentials/{principal.user_id}/{exchange}",
                        headers=headers,
                    )
                )
            except Exception:
                continue
        execution = order_client.get(
            "/admin/execution/config",
            headers=build_internal_admin_headers(principal, "/admin/execution/config"),
        ) if "admin" in principal.roles else {
            "live_trading_enabled": settings.live_trading_enabled,
            "default_shadow_mode": settings.default_shadow_mode,
            "allowed_exchanges": list(settings.allowed_live_exchanges),
            "strict_runtime": settings.strict_runtime,
        }
        summary["settings"] = {
            "credentials": credentials,
            "execution": execution,
        }
    except Exception as exc:
        summary["settings_error"] = str(exc)

    return summary
