from fastapi import APIRouter
from app.core.engine import build_summary

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/orchestrator/summary")
def summary():
    return build_summary()
