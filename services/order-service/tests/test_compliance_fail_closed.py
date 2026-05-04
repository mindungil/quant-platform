"""Tests for fail-closed compliance behaviour in order-service engine.

After the P0 fix, the compliance gateway blocks live orders when the
gateway is unavailable (fail-closed), while shadow orders remain
fail-open to avoid disrupting observation.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


def _make_payload(shadow_mode: bool = False):
    p = MagicMock()
    p.asset = "ETHUSDT"
    p.side = "BUY"
    p.requested_notional = 1000.0
    p.shadow_mode = shadow_mode
    return p


class TestFailClosed:
    """Compliance gateway fail-closed behaviour."""

    def test_live_order_blocked_when_gateway_unavailable(self):
        """Live order must be blocked when compliance gateway is not initialised."""
        os.environ["COMPLIANCE_ENABLED"] = "true"
        os.environ["COMPLIANCE_FAIL_CLOSED"] = "true"
        try:
            from app.core.engine import _compliance_check

            with patch("app.core.engine._get_compliance_gateway", return_value=False):
                result = _compliance_check(_make_payload(shadow_mode=False))
                assert result is not None
                assert result["approved"] is False
                assert result["reason"] == "compliance_gateway_unavailable"
        finally:
            os.environ.pop("COMPLIANCE_FAIL_CLOSED", None)
            os.environ["COMPLIANCE_ENABLED"] = "true"

    def test_shadow_order_passes_when_gateway_unavailable(self):
        """Shadow order should pass (fail-open) even when gateway is unavailable."""
        os.environ["COMPLIANCE_ENABLED"] = "true"
        os.environ["COMPLIANCE_FAIL_CLOSED"] = "true"
        try:
            from app.core.engine import _compliance_check

            with patch("app.core.engine._get_compliance_gateway", return_value=False):
                result = _compliance_check(_make_payload(shadow_mode=True))
                assert result is None  # fail-open for shadow
        finally:
            os.environ.pop("COMPLIANCE_FAIL_CLOSED", None)

    def test_live_order_blocked_when_check_throws(self):
        """Live order must be blocked when compliance check raises an exception."""
        os.environ["COMPLIANCE_ENABLED"] = "true"
        os.environ["COMPLIANCE_FAIL_CLOSED"] = "true"
        try:
            from app.core.engine import _compliance_check

            gw_mock = MagicMock()
            gw_mock.check.side_effect = RuntimeError("state provider timeout")

            with patch("app.core.engine._get_compliance_gateway", return_value=gw_mock):
                result = _compliance_check(_make_payload(shadow_mode=False))
                assert result is not None
                assert result["approved"] is False
                assert result["reason"] == "compliance_check_exception"
        finally:
            os.environ.pop("COMPLIANCE_FAIL_CLOSED", None)

    def test_shadow_order_passes_when_check_throws(self):
        """Shadow order should pass even when check throws."""
        os.environ["COMPLIANCE_ENABLED"] = "true"
        os.environ["COMPLIANCE_FAIL_CLOSED"] = "true"
        try:
            from app.core.engine import _compliance_check

            gw_mock = MagicMock()
            gw_mock.check.side_effect = RuntimeError("state provider timeout")

            with patch("app.core.engine._get_compliance_gateway", return_value=gw_mock):
                result = _compliance_check(_make_payload(shadow_mode=True))
                assert result is None
        finally:
            os.environ.pop("COMPLIANCE_FAIL_CLOSED", None)

    def test_legacy_fail_open_mode(self):
        """COMPLIANCE_FAIL_CLOSED=false reverts to legacy fail-open for live."""
        os.environ["COMPLIANCE_ENABLED"] = "true"
        os.environ["COMPLIANCE_FAIL_CLOSED"] = "false"
        try:
            from app.core.engine import _compliance_check

            with patch("app.core.engine._get_compliance_gateway", return_value=False):
                result = _compliance_check(_make_payload(shadow_mode=False))
                assert result is None  # fail-open: legacy behaviour
        finally:
            os.environ.pop("COMPLIANCE_FAIL_CLOSED", None)

    def test_disabled_compliance_always_passes(self):
        """COMPLIANCE_ENABLED=false → None regardless of fail_closed setting."""
        os.environ["COMPLIANCE_ENABLED"] = "false"
        os.environ["COMPLIANCE_FAIL_CLOSED"] = "true"
        try:
            from app.core.engine import _compliance_check

            result = _compliance_check(_make_payload(shadow_mode=False))
            assert result is None
        finally:
            os.environ["COMPLIANCE_ENABLED"] = "true"
            os.environ.pop("COMPLIANCE_FAIL_CLOSED", None)
