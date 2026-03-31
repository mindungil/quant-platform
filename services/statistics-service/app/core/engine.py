from app.models.statistics import StatisticsInput, StatisticsSnapshot


def compute_statistics(payload: StatisticsInput) -> StatisticsSnapshot:
    trade_count = len(payload.trade_pnls)
    total_return = round(sum(payload.trade_pnls), 4)
    win_rate = 0.0 if trade_count == 0 else round(sum(1 for pnl in payload.trade_pnls if pnl > 0) / trade_count, 4)
    drift = trade_count > 0 and total_return < payload.expected_return
    mean_return = 0.0 if trade_count == 0 else total_return / trade_count
    variance = 0.0 if trade_count == 0 else sum((pnl - mean_return) ** 2 for pnl in payload.trade_pnls) / trade_count
    downside = [pnl for pnl in payload.trade_pnls if pnl < 0]
    downside_variance = 0.0 if not downside else sum(pnl**2 for pnl in downside) / len(downside)
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in payload.trade_pnls:
        running += pnl
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
    return StatisticsSnapshot(
        user_id=payload.user_id,
        trade_count=trade_count,
        total_return=total_return,
        win_rate=win_rate,
        drift_detected=drift,
        sharpe=0.0 if variance == 0 else round(mean_return / (variance**0.5), 4),
        sortino=0.0 if downside_variance == 0 else round(mean_return / (downside_variance**0.5), 4),
        max_drawdown=round(abs(max_drawdown), 4),
        recent_trade_pnls=payload.trade_pnls[-10:],
    )
