from app.models.risk import RiskApprovalRequest, RiskApprovalResponse


def approve_order(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    if payload.current_drawdown >= 0.10:
        return RiskApprovalResponse(approved=False, reason="liquidate_threshold_reached", level="LIQUIDATE")
    if payload.current_drawdown >= 0.05:
        return RiskApprovalResponse(approved=False, reason="warning_threshold_reached", level="HALT")
    if payload.requested_notional > payload.max_notional:
        return RiskApprovalResponse(approved=False, reason="notional_limit_exceeded", level="HALT")
    return RiskApprovalResponse(approved=True, reason="approved", level="OK")
