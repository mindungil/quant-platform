from fastapi import APIRouter
from app.core.engine import approve_order
from app.models.risk import RiskApprovalRequest, RiskApprovalResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/risk/approve", response_model=RiskApprovalResponse)
def approve(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    return approve_order(payload)
