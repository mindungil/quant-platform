from fastapi import APIRouter
from app.core.summary import gateway_summary

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/gateway/summary")
def summary() -> dict:
    return gateway_summary()
