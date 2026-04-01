import time

from prometheus_client import Counter, Histogram

from app.models.risk import RiskApprovalRequest, RiskApprovalResponse
from app.db.repository import risk_repository

risk_approvals_total = Counter(
    "risk_approvals_total",
    "Total risk approval decisions",
    ["result", "level"],
)
risk_approval_latency_seconds = Histogram(
    "risk_approval_latency_seconds",
    "Latency of risk approval evaluation",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


def _record_metrics(result: RiskApprovalResponse, start: float) -> None:
    label_result = "approved" if result.approved else "rejected"
    risk_approvals_total.labels(result=label_result, level=result.level).inc()
    risk_approval_latency_seconds.observe(time.monotonic() - start)


def approve_order(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    _start = time.monotonic()
    exposure_ratio = 0.0 if payload.exposure_limit == 0 else payload.current_exposure / payload.exposure_limit
    if not payload.automation_enabled:
        result = RiskApprovalResponse(
            approved=False,
            reason="automation_disabled",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        _record_metrics(result, _start)
        return result
    if payload.current_drawdown >= 0.10:
        result = RiskApprovalResponse(
            approved=False,
            reason="liquidate_threshold_reached",
            level="LIQUIDATE",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        _record_metrics(result, _start)
        return result
    if payload.current_drawdown >= 0.05:
        result = RiskApprovalResponse(
            approved=False,
            reason="warning_threshold_reached",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        _record_metrics(result, _start)
        return result
    if payload.requested_notional > payload.max_notional:
        result = RiskApprovalResponse(
            approved=False,
            reason="notional_limit_exceeded",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        _record_metrics(result, _start)
        return result
    if payload.current_exposure + payload.requested_notional > payload.exposure_limit:
        result = RiskApprovalResponse(
            approved=False,
            reason="exposure_limit_exceeded",
            level="HALT",
            exposure_ratio=round(exposure_ratio, 4),
        )
        risk_repository.record(payload, result)
        _record_metrics(result, _start)
        return result
    result = RiskApprovalResponse(
        approved=True,
        reason="approved",
        level="OK",
        exposure_ratio=round(exposure_ratio, 4),
    )
    risk_repository.record(payload, result)
    _record_metrics(result, _start)
    return result
