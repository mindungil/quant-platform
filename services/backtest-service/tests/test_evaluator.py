from app.core.evaluator import evaluate_strategy
from app.models.backtest import BacktestRequest


def test_backtest_returns_result_with_real_metrics() -> None:
    """Real backtester should produce valid metrics from synthetic data."""
    result = evaluate_strategy(
        BacktestRequest(strategy_id="s1", weights={"rsi": 1.2, "macd": 1.1}, sample_size=500)
    )
    # Should return a valid result with real numeric metrics
    assert result.strategy_id == "s1"
    assert isinstance(result.sharpe_ratio, float)
    assert isinstance(result.sortino_ratio, float)
    assert 0.0 <= result.max_drawdown <= 1.0
    assert 0.0 <= result.win_rate <= 1.0
    assert result.trade_count >= 0
    assert isinstance(result.total_return, float)
    assert isinstance(result.avg_trade_pnl, float)
    assert result.status in ("PASSED", "FAILED")
