from app.models.statistics import StatisticsInput, StatisticsSnapshot


def compute_statistics(payload: StatisticsInput) -> StatisticsSnapshot:
    trade_count = len(payload.trade_pnls)
    total_return = round(sum(payload.trade_pnls), 4)
    win_rate = 0.0 if trade_count == 0 else round(sum(1 for pnl in payload.trade_pnls if pnl > 0) / trade_count, 4)
    drift = trade_count > 0 and total_return < payload.expected_return
    return StatisticsSnapshot(
        trade_count=trade_count,
        total_return=total_return,
        win_rate=win_rate,
        drift_detected=drift,
    )
