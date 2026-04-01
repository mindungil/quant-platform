from __future__ import annotations

from collections import defaultdict

from app.core.config import settings
from app.db.repository import orchestrator_repository
from app.services.health_client import check_service_health


# ---------------------------------------------------------------------------
# Service registry
# ---------------------------------------------------------------------------

def _downstream_services() -> dict[str, str]:
    """Return name -> base_url mapping for all known downstream services."""
    services: dict[str, str] = {
        "portfolio": settings.portfolio_service_base_url,
        "statistics": settings.statistics_service_base_url,
        "risk": settings.risk_service_base_url,
    }
    crypto_url = getattr(settings, "crypto_agent_base_url", None)
    if crypto_url:
        services["crypto-agent"] = crypto_url
    etf_url = getattr(settings, "etf_agent_base_url", None)
    if etf_url:
        services["etf-agent"] = etf_url
    stock_url = getattr(settings, "stock_agent_base_url", None)
    if stock_url:
        services["stock-agent"] = stock_url
    return services


# ---------------------------------------------------------------------------
# Health aggregation
# ---------------------------------------------------------------------------

def _aggregate_health() -> dict[str, dict]:
    """Call /health on every downstream service and return results keyed by name."""
    results: dict[str, dict] = {}
    for name, url in _downstream_services().items():
        results[name] = check_service_health(url)
    return results


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def detect_conflicts(agent_states: list[dict]) -> list[dict]:
    """Detect if multiple agents are attempting to trade the same asset.

    *agent_states* is a list of dicts, each containing at least
    ``agent``, ``asset``, and ``action`` keys.  Returns a (possibly empty)
    list of conflict descriptions.
    """
    asset_actions: dict[str, list[dict]] = defaultdict(list)
    for state in agent_states:
        asset = state.get("asset")
        action = state.get("action")
        if asset and action and action != "HOLD":
            asset_actions[asset].append(state)

    conflicts: list[dict] = []
    for asset, entries in asset_actions.items():
        if len(entries) > 1:
            agents = [e.get("agent", "unknown") for e in entries]
            actions = [e.get("action", "unknown") for e in entries]
            conflicts.append({
                "asset": asset,
                "agents": agents,
                "actions": actions,
                "message": f"Conflict: agents {agents} are targeting {asset} with actions {actions}",
            })
    return conflicts


# ---------------------------------------------------------------------------
# Agent status
# ---------------------------------------------------------------------------

def get_all_agent_statuses() -> dict:
    """Return health + availability for every registered agent service."""
    agent_keys = ["crypto-agent", "etf-agent", "stock-agent"]
    services = _downstream_services()
    statuses: dict[str, dict] = {}
    for key in agent_keys:
        url = services.get(key)
        if url is None:
            statuses[key] = {"configured": False}
            continue
        health = check_service_health(url)
        statuses[key] = {
            "configured": True,
            **health,
        }
    return statuses


# ---------------------------------------------------------------------------
# Summary (enhanced)
# ---------------------------------------------------------------------------

def build_summary() -> dict:
    health_results = _aggregate_health()
    all_healthy = all(v.get("healthy", False) for v in health_results.values())

    summary = {
        "status": "ok" if all_healthy else "degraded",
        "services": health_results,
        "agents": get_all_agent_statuses(),
        "conflicts": [],
        "message": "orchestrator coordination summary",
    }

    snapshot = orchestrator_repository.save(summary)
    summary["snapshot_id"] = snapshot["snapshot_id"]
    return summary
