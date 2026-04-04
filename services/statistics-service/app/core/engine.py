import math
import numpy as np
# Patch numpy for empyrical compatibility with NumPy 2.x
if not hasattr(np, "NINF"):
    np.NINF = -np.inf  # type: ignore[attr-defined]
if not hasattr(np, "PINF"):
    np.PINF = np.inf  # type: ignore[attr-defined]
import empyrical as ep

from prometheus_client import Counter, Gauge

from app.models.statistics import StatisticsInput, StatisticsSnapshot

strategy_drift_score = Gauge(
    "strategy_drift_score",
    "Strategy drift score vs backtest baseline",
    ["strategy_id"],
)
strategy_drift_alert = Gauge(
    "strategy_drift_alert",
    "Strategy drift alert level (0=green, 1=yellow, 2=red)",
    ["strategy_id"],
)
drift_alert_threshold = Gauge(
    "drift_alert_threshold",
    "Current drift alert threshold value",
    ["strategy_id"],
)
statistics_computations_total = Counter(
    "statistics_computations_total",
    "Total statistics computation events",
)

# Optional async callback for publishing drift events (set at startup)
_drift_event_callback = None


def set_drift_event_callback(cb):
    global _drift_event_callback
    _drift_event_callback = cb


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

    returns = np.array(payload.trade_pnls)
    total_return = round(float(np.sum(returns)), 4)

    # Win/loss classification
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = round(float(len(wins) / trade_count), 4)

    # --- empyrical: production-grade performance metrics ---
    sharpe = round(float(ep.sharpe_ratio(returns)), 4) if trade_count > 1 else 0.0
    sortino = round(float(ep.sortino_ratio(returns)), 4) if trade_count > 1 else 0.0
    max_drawdown = round(float(abs(ep.max_drawdown(returns))), 4)
    calmar = round(float(ep.calmar_ratio(returns)), 4) if max_drawdown > 0 and trade_count > 1 else 0.0

    # Clamp extreme values from small samples
    sharpe = max(-10.0, min(10.0, sharpe))
    sortino = max(-10.0, min(10.0, sortino))
    calmar = max(-100.0, min(100.0, calmar))

    # --- Additional metrics (not in empyrical) ---
    # Profit factor
    gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else 999.0

    # Win/loss averages and payoff ratio
    avg_win = round(float(np.mean(wins)), 6) if len(wins) > 0 else 0.0
    avg_loss = round(float(np.mean(losses)), 6) if len(losses) > 0 else 0.0
    payoff_ratio = round(avg_win / abs(avg_loss), 4) if avg_loss != 0 else 999.0

    # Expectancy
    loss_rate = 1 - win_rate
    expectancy = round((win_rate * avg_win) - (loss_rate * abs(avg_loss)), 6)

    # Drift detection
    drift = total_return < payload.expected_return

    statistics_computations_total.inc()
    # Update drift gauges
    drift_score = abs(total_return - payload.expected_return)
    sid = payload.strategy_id or "default"
    strategy_drift_score.labels(strategy_id=sid).set(round(drift_score, 4))
    if drift_score > 0.1:
        strategy_drift_alert.labels(strategy_id=sid).set(2)  # red
        drift_alert_threshold.labels(strategy_id=sid).set(0.1)
    elif drift_score > 0.05:
        strategy_drift_alert.labels(strategy_id=sid).set(1)  # yellow
        drift_alert_threshold.labels(strategy_id=sid).set(0.05)
    else:
        strategy_drift_alert.labels(strategy_id=sid).set(0)  # green
        drift_alert_threshold.labels(strategy_id=sid).set(0.05)

    # Publish drift alert event for critical drift
    if drift_score > 0.1 and _drift_event_callback is not None:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_drift_event_callback(
                    "strategy.drift_alert",
                    {
                        "strategy_id": sid,
                        "asset": getattr(payload, "asset", ""),
                        "drift_score": round(drift_score, 4),
                        "threshold": 0.1,
                        "total_return": total_return,
                        "expected_return": payload.expected_return,
                    },
                ))
        except Exception:
            pass  # drift event is best-effort

    return StatisticsSnapshot(
        user_id=payload.user_id,
        strategy_id=payload.strategy_id,
        trade_count=trade_count,
        total_return=total_return,
        win_rate=win_rate,
        drift_detected=drift,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        calmar_ratio=calmar,
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=payoff_ratio,
        expectancy=expectancy,
        recent_trade_pnls=payload.trade_pnls[-10:],
    )
