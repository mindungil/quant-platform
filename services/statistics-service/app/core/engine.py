import math
from app.models.statistics import StatisticsInput, StatisticsSnapshot


# Annual risk-free rate (US Treasury ~5%)
RISK_FREE_RATE_ANNUAL = 0.05
RISK_FREE_RATE_DAILY = (1 + RISK_FREE_RATE_ANNUAL) ** (1 / 252) - 1
ANNUALIZATION_FACTOR = math.sqrt(252)


def compute_statistics(payload: StatisticsInput) -> StatisticsSnapshot:
    trade_count = len(payload.trade_pnls)

    if trade_count == 0:
        return StatisticsSnapshot(
            user_id=payload.user_id,
            strategy_id=payload.strategy_id,
            trade_count=0,
            total_return=0.0,
            win_rate=0.0,
            drift_detected=False,
            recent_trade_pnls=[],
        )

    returns = payload.trade_pnls
    total_return = round(sum(returns), 4)

    # Win/loss classification
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round(win_count / trade_count, 4)

    # Mean return
    mean_return = total_return / trade_count

    # --- Sharpe Ratio (annualized, sample std, excess returns) ---
    excess_returns = [r - RISK_FREE_RATE_DAILY for r in returns]
    mean_excess = sum(excess_returns) / len(excess_returns)
    if trade_count > 1:
        variance = sum((r - mean_excess) ** 2 for r in excess_returns) / (trade_count - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 1e-9
    else:
        std_dev = 1e-9
    sharpe = round(mean_excess / std_dev * ANNUALIZATION_FACTOR, 4)

    # --- Sortino Ratio (annualized, downside deviation) ---
    downside_returns = [r for r in excess_returns if r < 0]
    if len(downside_returns) > 1:
        downside_variance = sum(r ** 2 for r in downside_returns) / (len(downside_returns) - 1)
        downside_dev = math.sqrt(downside_variance) if downside_variance > 0 else 1e-9
    else:
        downside_dev = 1e-9
    sortino = round(mean_excess / downside_dev * ANNUALIZATION_FACTOR, 4)

    # --- Max Drawdown ---
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in returns:
        running += pnl
        peak = max(peak, running)
        dd = (peak - running) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, dd)

    # --- Profit Factor ---
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else 999.0

    # --- Calmar Ratio ---
    annualized_return = mean_return * 252
    calmar = round(annualized_return / max_drawdown, 4) if max_drawdown > 0 else 0.0

    # --- Average Win / Average Loss / Payoff Ratio ---
    avg_win = round(sum(wins) / win_count, 6) if win_count > 0 else 0.0
    avg_loss = round(sum(losses) / loss_count, 6) if loss_count > 0 else 0.0
    payoff_ratio = round(avg_win / abs(avg_loss), 4) if avg_loss != 0 else 999.0

    # --- Expectancy ---
    loss_rate = 1 - win_rate
    expectancy = round((win_rate * avg_win) - (loss_rate * abs(avg_loss)), 6)

    # --- Drift detection ---
    drift = total_return < payload.expected_return

    return StatisticsSnapshot(
        user_id=payload.user_id,
        strategy_id=payload.strategy_id,
        trade_count=trade_count,
        total_return=total_return,
        win_rate=win_rate,
        drift_detected=drift,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=round(max_drawdown, 4),
        profit_factor=profit_factor,
        calmar_ratio=calmar,
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=payoff_ratio,
        expectancy=expectancy,
        recent_trade_pnls=returns[-10:],
    )
