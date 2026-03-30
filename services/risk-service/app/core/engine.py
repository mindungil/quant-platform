from app.models.risk import RiskApprovalRequest, RiskApprovalResponse


def approve_order(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    exposure_ratio = 0.0 if payload.exposure_limit == 0 else payload.current_exposure / payload.exposure_limit
    if not payload.automation_enabled:
        return RiskApprovalResponse(
            approved=False,
            reason="automation_disabled",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
    if payload.current_drawdown >= 0.10:
        return RiskApprovalResponse(
            approved=False,
            reason="liquidate_threshold_reached",
            level="LIQUIDATE",
            exposure_ratio=round(exposure_ratio, 4),
        )
    if payload.current_drawdown >= 0.05:
        return RiskApprovalResponse(
            approved=False,
            reason="warning_threshold_reached",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
    if payload.requested_notional > payload.max_notional:
        return RiskApprovalResponse(
            approved=False,
            reason="notional_limit_exceeded",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
    if payload.current_exposure + payload.requested_notional > payload.exposure_limit:
        return RiskApprovalResponse(
            approved=False,
            reason="exposure_limit_exceeded",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
    return RiskApprovalResponse(
        approved=True,
        reason="approved",
        level="OK",
        exposure_ratio=round(exposure_ratio, 4),
    )
