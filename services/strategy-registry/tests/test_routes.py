"""Multi-tenancy regression tests for strategy-registry routes."""
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


def _make_strategy(strategy_id: str, user_id: str, status: str = "ACTIVE"):
    mock = MagicMock()
    mock.id = strategy_id
    mock.user_id = user_id
    mock.status = status
    mock.name = "Test Strategy"
    mock.asset_type = "crypto"
    mock.indicators = ["rsi_14"]
    mock.weights = {"rsi": 1.0}
    mock.thresholds = {"entry": 0.6, "exit": -0.6}
    mock.version = "v1"
    mock.backtest_results = {}
    mock.shadow_metrics = {}
    mock.shadow_start_at = None
    mock.created_at = "2025-01-01T00:00:00Z"
    mock.updated_at = "2025-01-01T00:00:00Z"
    # model_dump for FastAPI serialization
    mock.model_dump.return_value = {
        "id": strategy_id,
        "user_id": user_id,
        "status": status,
        "name": "Test Strategy",
        "asset_type": "crypto",
        "indicators": ["rsi_14"],
        "weights": {"rsi": 1.0},
        "thresholds": {"entry": 0.6, "exit": -0.6},
        "version": "v1",
        "backtest_results": {},
        "shadow_metrics": {},
        "shadow_start_at": None,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }
    return mock


@patch("app.services.shadow_tracker.shadow_tracker")
@patch("app.services.drift_consumer.drift_consumer")
def _get_client(*_mocks):
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


_client = _get_client()


@patch("app.db.repository.strategy_repository")
def test_get_strategy_no_user_id_non_bootstrap(mock_repo):
    """Non-bootstrap strategy without x-user-id header returns 403."""
    mock_repo.get.return_value = _make_strategy("s-1", "some-user")
    resp = _client.get("/strategies/s-1")
    assert resp.status_code == 403


@patch("app.db.repository.strategy_repository")
def test_get_strategy_no_user_id_bootstrap(mock_repo):
    """Bootstrap strategy without x-user-id header returns 200."""
    mock_repo.get.return_value = _make_strategy("s-1", "bootstrap")
    resp = _client.get("/strategies/s-1")
    assert resp.status_code == 200


@patch("app.db.repository.strategy_repository")
def test_delete_strategy_wrong_user(mock_repo):
    """Deleting another user's strategy returns 404 (not leaked info)."""
    mock_repo.get.return_value = _make_strategy("s-1", "user-B")
    resp = _client.delete("/strategies/s-1", headers={"x-user-id": "user-A"})
    assert resp.status_code == 404
