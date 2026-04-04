"""Orchestrator Agent — Multi-agent coordinator.

Manages the lifecycle and coordination of all trading agents.
Responsibilities:
1. Monitor agent health and performance
2. Detect and resolve conflicts between agents
3. Enforce portfolio-level risk limits across all agents
4. Track and report overall system state
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from collections import defaultdict

import httpx

from app.core.config import settings
from app.db.repository import orchestrator_repository

logger = logging.getLogger("orchestrator-agent")

AGENT_REGISTRY = {
    "crypto-agent": {
        "base_url": settings.crypto_agent_base_url,
        "asset_types": ["crypto"],
        "enabled": True,
    },
    "etf-agent": {
        "base_url": settings.etf_agent_base_url,
        "asset_types": ["etf"],
        "enabled": True,
    },
    "stock-agent": {
        "base_url": settings.stock_agent_base_url,
        "asset_types": ["stock"],
        "enabled": True,
    },
}


def _probe(name: str, url: str) -> dict:
    """Probe a service for health."""
    try:
        r = httpx.get(f"{url}/health", timeout=3.0)
        r.raise_for_status()
        data = r.json()
        return {"status": data.get("status", "ok"), "latency_ms": r.elapsed.total_seconds() * 1000}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:100]}


def _get_agent_status(name: str, url: str) -> dict:
    """Get agent scheduler status."""
    try:
        r = httpx.get(f"{url}/agent/status", timeout=5.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"running": False, "error": "unreachable"}


def _get_portfolio_risk() -> dict:
    """Check portfolio-level risk across all agents."""
    try:
        r = httpx.get(f"{settings.portfolio_service_base_url}/portfolio/anonymous", timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            return {
                "total_exposure": data.get("total_exposure", 0),
                "concentration": data.get("concentration", {}),
                "rebalance_needed": data.get("rebalance_needed", False),
                "largest_position": data.get("largest_position", ""),
            }
    except Exception:
        pass
    return {"total_exposure": 0}


def _get_recent_decisions(agent_url: str, asset: str = "BTCUSDT") -> list[dict]:
    """Get recent decisions from an agent."""
    try:
        r = httpx.get(f"{agent_url}/decisions/history/{asset}", timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[-5:]
    except Exception:
        pass
    return []


def _fetch_agent_win_rate(agent_name: str) -> float | None:
    """Fetch win_rate for an agent from statistics-service."""
    try:
        r = httpx.get(
            f"{settings.statistics_service_base_url}/statistics/agent/{agent_name}",
            timeout=3.0,
        )
        if r.status_code == 200:
            data = r.json()
            wr = data.get("win_rate")
            if wr is not None:
                return float(wr)
    except Exception:
        pass
    return None


def resolve_conflict(losing_agent_url: str, asset: str, reason: str) -> bool:
    """Tell the losing agent to HOLD on the conflicting asset."""
    try:
        r = httpx.post(
            f"{losing_agent_url.rstrip('/')}/agent/override",
            json={"asset": asset, "force_action": "HOLD", "reason": reason},
            timeout=5.0,
        )
        return r.status_code == 200
    except Exception as exc:
        logger.warning("resolve_conflict_failed", extra={
            "asset": asset, "error": str(exc)[:100],
        })
        return False


def detect_conflicts(agent_decisions: dict[str, list[dict]]) -> list[dict]:
    """Detect when agents make conflicting decisions on the same asset."""
    conflicts = []
    asset_actions: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    for agent_name, decisions in agent_decisions.items():
        for d in decisions:
            asset = d.get("asset", "")
            action = d.get("action", "HOLD")
            timestamp = d.get("timestamp", "")
            if action != "HOLD":
                asset_actions[asset].append((agent_name, action, timestamp))

    for asset, actions in asset_actions.items():
        buys = [(a, ts) for a, act, ts in actions if act == "BUY"]
        sells = [(a, ts) for a, act, ts in actions if act == "SELL"]
        if buys and sells:
            # --- Conflict resolution: pick winner by win_rate or recency ---
            buy_agents = [a for a, _ in buys]
            sell_agents = [a for a, _ in sells]

            winner = None
            loser = None
            resolution_method = "none"

            # Try win_rate from statistics-service
            all_conflicting = buy_agents + sell_agents
            win_rates: dict[str, float] = {}
            for agent_name in all_conflicting:
                wr = _fetch_agent_win_rate(agent_name)
                if wr is not None:
                    win_rates[agent_name] = wr

            if len(win_rates) >= 2:
                best_agent = max(win_rates, key=win_rates.get)  # type: ignore[arg-type]
                worst_agent = min(win_rates, key=win_rates.get)  # type: ignore[arg-type]
                winner = best_agent
                loser = worst_agent
                resolution_method = "win_rate"
            else:
                # Fallback: prefer more recent decision
                all_with_ts = [(a, ts) for a, _, ts in actions if _ != "HOLD"]
                if all_with_ts:
                    all_with_ts.sort(key=lambda x: x[1], reverse=True)
                    winner = all_with_ts[0][0]
                    loser = all_with_ts[-1][0] if len(all_with_ts) > 1 else None
                    resolution_method = "recency"

            # Execute resolution
            resolution_result = None
            if loser and loser in AGENT_REGISTRY:
                loser_url = AGENT_REGISTRY[loser]["base_url"]
                ok = resolve_conflict(loser_url, asset, "orchestrator_conflict_resolution")
                resolution_result = {
                    "winner": winner,
                    "loser": loser,
                    "method": resolution_method,
                    "override_sent": ok,
                }
                logger.info("conflict_resolved", extra={
                    "asset": asset, **resolution_result,
                })

            conflicts.append({
                "asset": asset,
                "buy_agents": buy_agents,
                "sell_agents": sell_agents,
                "severity": "high",
                "recommendation": "두 에이전트가 상반된 의사결정. 오케스트레이터가 최근 성과가 더 좋은 에이전트의 결정을 우선합니다.",
                "resolution": resolution_result,
            })

    return conflicts


def build_system_summary() -> dict:
    """Build comprehensive system summary for the dashboard."""
    timestamp = datetime.now(UTC)

    # Agent health & status
    agents = {}
    for name, info in AGENT_REGISTRY.items():
        health = _probe(name, info["base_url"])
        status = _get_agent_status(name, info["base_url"])
        recent = _get_recent_decisions(info["base_url"])
        agents[name] = {
            "health": health,
            "scheduler": status,
            "recent_decisions": recent,
            "enabled": info["enabled"],
            "asset_types": info["asset_types"],
        }

    # Downstream services
    downstream = {}
    for svc_name, url in [
        ("portfolio", settings.portfolio_service_base_url),
        ("statistics", settings.statistics_service_base_url),
        ("risk", settings.risk_service_base_url),
        ("signal", getattr(settings, "signal_service_base_url", None)),
        ("memory", getattr(settings, "memory_service_base_url", None)),
    ]:
        if url:
            downstream[svc_name] = _probe(svc_name, url)

    # Portfolio risk
    portfolio_risk = _get_portfolio_risk()

    # Conflict detection
    all_decisions = {}
    for name, info in agents.items():
        all_decisions[name] = info.get("recent_decisions", [])
    conflicts = detect_conflicts(all_decisions)

    # Overall system status
    all_healthy = all(
        a["health"].get("status") == "ok" for a in agents.values()
    )
    any_running = any(
        a["scheduler"].get("running", False) for a in agents.values()
    )

    summary = {
        "timestamp": timestamp.isoformat(),
        "system_status": "정상" if all_healthy and any_running else "점검 필요",
        "agents": agents,
        "downstream_services": downstream,
        "portfolio_risk": portfolio_risk,
        "conflicts": conflicts,
        "total_agents": len(agents),
        "active_agents": sum(1 for a in agents.values() if a["scheduler"].get("running")),
    }

    # Persist snapshot
    try:
        orchestrator_repository.save(summary)
    except Exception:
        pass

    return summary


# ---------------------------------------------------------------------------
# Legacy compat — keep old functions working for existing callers
# ---------------------------------------------------------------------------

def build_summary() -> dict:
    """Backward-compatible wrapper that delegates to build_system_summary."""
    return build_system_summary()


def get_all_agent_statuses() -> dict:
    """Return health + scheduler status for every registered agent."""
    statuses: dict[str, dict] = {}
    for name, info in AGENT_REGISTRY.items():
        health = _probe(name, info["base_url"])
        status = _get_agent_status(name, info["base_url"])
        statuses[name] = {
            "configured": True,
            "health": health,
            "scheduler": status,
            "enabled": info["enabled"],
        }
    return statuses


def check_pipeline_health() -> dict:
    """Check the full signal pipeline: market-data → feature-store → signal-service → crypto-agent."""
    import time as _time

    PIPELINE_STAGES = [
        ("market-data", settings.market_data_base_url),
        ("feature-store", settings.feature_store_base_url),
        ("signal-service", settings.signal_service_base_url),
        ("crypto-agent", settings.crypto_agent_base_url),
    ]
    EVENT_FRESHNESS_SECONDS = 600  # 10 minutes

    stages = []
    overall = "healthy"

    for name, base_url in PIPELINE_STAGES:
        stage: dict = {"name": name, "status": "healthy", "last_event_age_seconds": None}
        # (a) HTTP health check
        health = _probe(name, base_url)
        if health.get("status") != "ok":
            stage["status"] = "broken"
            stage["detail"] = health.get("detail", "health check failed")
            overall = "broken"
            stages.append(stage)
            continue

        # (b) Check last event freshness via service-specific endpoints
        try:
            r = httpx.get(f"{base_url}/health", timeout=3.0)
            data = r.json() if r.status_code == 200 else {}
            last_event = data.get("last_event_at") or data.get("last_updated")
            if last_event:
                from datetime import datetime as _dt, timezone as _tz
                if isinstance(last_event, str):
                    ts = _dt.fromisoformat(last_event.replace("Z", "+00:00"))
                else:
                    ts = _dt.fromtimestamp(last_event, tz=_tz.utc)
                age = (datetime.now(UTC) - ts).total_seconds()
                stage["last_event_age_seconds"] = round(age)
                if age > EVENT_FRESHNESS_SECONDS:
                    stage["status"] = "degraded"
                    if overall != "broken":
                        overall = "degraded"
        except Exception:
            pass  # freshness check is best-effort

        stages.append(stage)

    return {
        "pipeline_status": overall,
        "stages": stages,
    }
