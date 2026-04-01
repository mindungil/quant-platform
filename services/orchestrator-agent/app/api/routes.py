from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.engine import build_summary, get_all_agent_statuses
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


@router.get("/orchestrator/agents")
def agents():
    """Return the health/availability status of all registered agent services."""
    return get_all_agent_statuses()
