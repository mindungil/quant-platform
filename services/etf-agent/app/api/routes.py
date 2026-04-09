from fastapi import APIRouter

from app.core.engine import run_decision_loop
from app.core.market_hours import is_korean_market_open

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/agent/availability")
def availability() -> dict[str, bool]:
    return {"market_open": is_korean_market_open()}


@router.post("/decisions/run/{asset}")
def run_decision(asset: str):
    return run_decision_loop(asset)
