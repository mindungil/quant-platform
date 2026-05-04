"""Tests for compliance gateway integration in order-service engine."""
import os
from unittest.mock import MagicMock, patch

import pytest


def test_compliance_check_returns_none_when_disabled():
    """COMPLIANCE_ENABLED=false → None (fail-open)."""
    os.environ["COMPLIANCE_ENABLED"] = "false"
    try:
        from app.core.engine import _compliance_check
        payload = MagicMock()
        payload.asset = "ETHUSDT"
        payload.side = "BUY"
        payload.requested_notional = 1000.0
        result = _compliance_check(payload)
        assert result is None
    finally:
        os.environ["COMPLIANCE_ENABLED"] = "true"


def test_compliance_check_approves_small_order():
    """Small order within all limits → approved."""
    from shared.execution.compliance import ComplianceGateway, ComplianceLimits

    class StubState:
        def get_equity(self): return 100_000.0
        def get_positions(self): return {"ETHUSDT": 5000.0}
        def is_kill_switch_active(self): return False

    gw = ComplianceGateway(ComplianceLimits(), StubState())
    d = gw.check("ETHUSDT", "BUY", 500.0)
    assert d.approved is True


def test_compliance_check_blocks_kill_switch():
    """Kill switch active → blocked."""
    from shared.execution.compliance import ComplianceGateway, ComplianceLimits

    class KillState:
        def get_equity(self): return 100_000.0
        def get_positions(self): return {}
        def is_kill_switch_active(self): return True

    gw = ComplianceGateway(ComplianceLimits(), KillState())
    d = gw.check("ETHUSDT", "BUY", 500.0)
    assert d.approved is False
    assert d.reason == "kill_switch_active"


def test_compliance_check_blocks_oversized():
    """Order > max_order_qty_pct of equity → blocked."""
    from shared.execution.compliance import ComplianceGateway, ComplianceLimits

    class SmallEquity:
        def get_equity(self): return 1000.0
        def get_positions(self): return {}
        def is_kill_switch_active(self): return False

    gw = ComplianceGateway(ComplianceLimits(max_order_qty_pct=0.10), SmallEquity())
    d = gw.check("ETHUSDT", "BUY", 200.0)  # 20% of equity
    assert d.approved is False
    assert "order_too_large" in d.reason
