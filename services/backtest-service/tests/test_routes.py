"""Multi-tenancy regression tests for backtest-service routes."""
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def _make_job(job_id: str, user_id: str):
    mock = MagicMock()
    mock.job_id = job_id
    mock.strategy_id = "strat-1"
    mock.user_id = user_id
    mock.status = "COMPLETED"
    mock.result = None
    mock.error = None
    mock.created_at = "2025-01-01T00:00:00Z"
    mock.completed_at = None
    return mock


@patch("app.core.evaluator.get_job")
def test_get_backtest_wrong_user(mock_get_job):
    """User A cannot view User B's backtest result."""
    mock_get_job.return_value = _make_job("job-1", "user-B")
    resp = client.get("/backtests/job-1", headers={"x-user-id": "user-A"})
    assert resp.status_code == 403
