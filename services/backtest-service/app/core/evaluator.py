from app.models.backtest import BacktestRequest, BacktestResult


def evaluate_strategy(payload: BacktestRequest) -> BacktestResult:
    weight_count = max(len(payload.weights), 1)
    sharpe = round(1.0 + ((sum(payload.weights.values()) / weight_count) * 0.2), 4)
    mdd = round(max(0.02, 0.18 - (payload.sample_size / 5000)), 4)
    win_rate = round(min(0.9, 0.45 + (payload.sample_size / 5000)), 4)
    status = "PASSED" if sharpe >= 1.1 and mdd <= 0.15 and win_rate >= 0.52 else "FAILED"
    return BacktestResult(
        strategy_id=payload.strategy_id,
        sharpe_ratio=sharpe,
        sortino_ratio=round(sharpe + 0.1, 4),
        max_drawdown=mdd,
        win_rate=win_rate,
        status=status,
    )
