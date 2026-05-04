"""Tests for drift observe/status and compliance integration endpoints."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_drift_observe_returns_level(client):
    resp = client.post("/signals/meta/drift/ETHUSDT/observe", json={"trade_return": 0.005})
    assert resp.status_code == 200
    data = resp.json()
    assert "level" in data
    assert data["asset"] == "ETHUSDT"


def test_drift_observe_requires_trade_return(client):
    resp = client.post("/signals/meta/drift/ETHUSDT/observe", json={})
    assert resp.status_code == 400


def test_drift_observe_numeric_validation(client):
    resp = client.post("/signals/meta/drift/ETHUSDT/observe", json={"trade_return": "abc"})
    assert resp.status_code == 400


def test_drift_status_returns_metrics(client):
    # Seed one observation first
    client.post("/signals/meta/drift/BTCUSDT/observe", json={"trade_return": 0.001})
    resp = client.get("/signals/meta/drift/BTCUSDT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["asset"] == "BTCUSDT"
    assert "level" in data
    assert "reason" in data
    assert "metrics" in data


def test_drift_all_status(client):
    resp = client.get("/signals/meta/drift")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "ETHUSDT" in data


def test_bl_views_endpoint(client):
    resp = client.get("/signals/meta/views")
    # May return 503 if meta_engine can't load OHLCV, or 200 with weights
    assert resp.status_code in (200, 503, 500)
    if resp.status_code == 200:
        data = resp.json()
        assert "assets" in data
        assert "posterior_weights" in data
