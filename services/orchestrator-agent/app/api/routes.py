from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.engine import build_summary, build_system_summary, get_all_agent_statuses, check_pipeline_health, run_agent_graph
from app.db.repository import orchestrator_repository

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/orchestrator/summary")
def summary():
    return build_summary()


@router.get("/orchestrator/snapshots/latest")
def latest_snapshot():
    return orchestrator_repository.latest() or {"status": "empty"}


@router.get("/orchestrator/conflicts")
def check_conflicts():
    """Check for conflicts between agents."""
    summary = build_system_summary()
    return {"conflicts": summary.get("conflicts", []), "system_status": summary.get("system_status")}


@router.get("/orchestrator/agents")
def agents():
    """Return the health/availability status of all registered agent services."""
    return get_all_agent_statuses()


@router.get("/pipeline/health")
def pipeline_health():
    """Check the full signal pipeline health: market-data → feature-store → signal-service → crypto-agent."""
    return check_pipeline_health()


@router.post("/orchestrator/agent-graph/{asset}")
def agent_graph_run(asset: str, agent_type: str = "crypto"):
    """Trigger the agent graph for a specific asset and return execution summary."""
    return run_agent_graph(asset, agent_type)
