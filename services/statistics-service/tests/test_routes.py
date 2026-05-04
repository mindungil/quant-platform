from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from shared.internal_admin import build_internal_admin_headers


class _RepoStub:
    def record_trade(self, user_id: str, pnl: float, expected_return: float = 0.0, **kwargs) -> dict:
        return {
            "user_id": user_id,
            "strategy_id": kwargs.get("strategy_id"),
            "trade_count": 1,
            "total_return": pnl,
            "win_rate": 1.0,
            "drift_detected": False,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "calmar_ratio": 0.0,
            "avg_win": pnl,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "expectancy": pnl,
            "value_at_risk": 0.0,
            "conditional_value_at_risk": 0.0,
            "recent_trade_pnls": [pnl],
        }

    def get_agent_stats(self, agent_name: str) -> dict:
        return {"agent_name": agent_name, "trade_count": 0, "win_rate": None, "total_return": 0.0, "sharpe": 0.0}


def _client(monkeypatch) -> TestClient:
    import app.api.routes as routes_module

    monkeypatch.setattr(routes_module, "statistics_repository", _RepoStub())
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_statistics_record_requires_internal_signature(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/statistics/record",
        json={"user_id": "user-1", "trade_pnls": [1.0]},
    )
    assert response.status_code == 403


def test_statistics_record_accepts_signed_internal_request(monkeypatch) -> None:
    client = _client(monkeypatch)
    import app.api.routes as routes_module

    response = client.post(
        "/statistics/record",
        json={"user_id": "user-1", "strategy_id": "s1", "agent_name": "crypto-agent", "lane": "agent_core", "trade_pnls": [12.5]},
        headers=build_internal_admin_headers(
            routes_module.settings.internal_admin_secret,
            "user-1",
            "/statistics/record",
        ),
    )
    assert response.status_code == 200
    assert response.json()["total_return"] == 12.5
