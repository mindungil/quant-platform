from fastapi import APIRouter
from app.core.market_hours import is_market_open

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/agent/availability")
def availability() -> dict[str, bool]:
    return {"market_open": is_market_open()}
