from app.core.evaluator import evaluate_strategy
from app.models.backtest import BacktestRequest


def test_backtest_returns_passed_for_reasonable_strategy() -> None:
    result = evaluate_strategy(
        BacktestRequest(strategy_id="s1", weights={"rsi": 1.2, "macd": 1.1}, sample_size=365)
    )
    assert result.status == "PASSED"
