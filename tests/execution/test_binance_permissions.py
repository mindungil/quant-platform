"""Binance API key permission validator tests.

Verifies the validate_permissions() guard refuses keys that have
withdraw or transfer enabled — a pure self-custody check that runs
before any --live execution.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.execution.binance_futures import BinanceFuturesConnector


def _make(testnet: bool = False):
    return BinanceFuturesConnector(api_key="k", api_secret="s", testnet=testnet)


def test_testnet_skips_check():
    conn = _make(testnet=True)
    result = conn.validate_permissions()
    assert result == {"_skipped": "testnet"}


def test_safe_key_passes():
    conn = _make(testnet=False)
    safe = {
        "enableWithdrawals": False,
        "enableInternalTransfer": False,
        "enableFutures": True,
        "ipRestrict": True,
    }
    with patch.object(conn, "_sapi_signed_get", return_value=safe):
        result = conn.validate_permissions()
    assert result == safe


def test_withdrawals_enabled_rejected():
    conn = _make(testnet=False)
    bad = {
        "enableWithdrawals": True,
        "enableInternalTransfer": False,
        "enableFutures": True,
    }
    with patch.object(conn, "_sapi_signed_get", return_value=bad):
        with pytest.raises(PermissionError, match="WITHDRAWAL"):
            conn.validate_permissions()


def test_internal_transfer_enabled_rejected():
    conn = _make(testnet=False)
    bad = {
        "enableWithdrawals": False,
        "enableInternalTransfer": True,
        "enableFutures": True,
    }
    with patch.object(conn, "_sapi_signed_get", return_value=bad):
        with pytest.raises(PermissionError, match="INTERNAL_TRANSFER"):
            conn.validate_permissions()


def test_futures_disabled_rejected():
    conn = _make(testnet=False)
    bad = {
        "enableWithdrawals": False,
        "enableInternalTransfer": False,
        "enableFutures": False,
    }
    with patch.object(conn, "_sapi_signed_get", return_value=bad):
        with pytest.raises(PermissionError, match="Futures"):
            conn.validate_permissions()


def test_endpoint_failure_rejected():
    """If we can't read the restrictions, refuse — opaque key = unsafe key."""
    conn = _make(testnet=False)
    with patch.object(conn, "_sapi_signed_get", side_effect=RuntimeError("network down")):
        with pytest.raises(PermissionError, match="Cannot read API key permissions"):
            conn.validate_permissions()
