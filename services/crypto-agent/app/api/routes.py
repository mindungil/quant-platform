from fastapi import APIRouter, HTTPException

from app.core.engine import run_decision_loop
from app.db.repository import decision_repository

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/decisions/run/{asset}")
def run_decision(asset: str):
    return run_decision_loop(asset)


@router.get("/decisions/latest/{asset}")
def get_latest_decision(asset: str):
    decision = decision_repository.get_latest(asset)
    if decision is None:
        raise HTTPException(status_code=404, detail="decision_not_found")
    return decision


@router.get("/decisions/history/{asset}")
def get_decision_history(asset: str):
    return decision_repository.get_history(asset)
