from app.core.engine import approve_order
from app.models.risk import RiskApprovalRequest


def test_risk_rejects_large_drawdown() -> None:
    result = approve_order(
        RiskApprovalRequest(asset="BTCUSDT", requested_notional=100, max_notional=1000, current_drawdown=0.11)
    )
    assert result.approved is False
    assert result.level == "LIQUIDATE"
