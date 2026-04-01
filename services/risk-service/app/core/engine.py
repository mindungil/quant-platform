from app.models.risk import RiskApprovalRequest, RiskApprovalResponse
from app.db.repository import risk_repository


def approve_order(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    exposure_ratio = 0.0 if payload.exposure_limit == 0 else payload.current_exposure / payload.exposure_limit
    if not payload.automation_enabled:
        result = RiskApprovalResponse(
            approved=False,
            reason="automation_disabled",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        return result
    if payload.current_drawdown >= 0.10:
        result = RiskApprovalResponse(
            approved=False,
            reason="liquidate_threshold_reached",
            level="LIQUIDATE",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        return result
    if payload.current_drawdown >= 0.05:
        result = RiskApprovalResponse(
            approved=False,
            reason="warning_threshold_reached",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        return result
    if payload.requested_notional > payload.max_notional:
        result = RiskApprovalResponse(
            approved=False,
            reason="notional_limit_exceeded",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        return result
    if payload.current_exposure + payload.requested_notional > payload.exposure_limit:
        result = RiskApprovalResponse(
            approved=False,
            reason="exposure_limit_exceeded",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        return result
    result = RiskApprovalResponse(
        approved=True,
        reason="approved",
        level="OK",
        exposure_ratio=round(exposure_ratio, 4),
    )
    risk_repository.record(payload, result)
    return result
