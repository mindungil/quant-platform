import math
import numpy as np
import pandas as pd
import quantstats as qs

from prometheus_client import Counter, Gauge

from app.models.statistics import StatisticsInput, StatisticsSnapshot


def _safe_float(val, default: float = 0.0) -> float:
    """Convert quantstats result to float, returning *default* for NaN/Inf."""
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default

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

    # Build a dated Series for quantstats (requires DatetimeIndex)
    ret_series = pd.Series(
        returns,
        index=pd.date_range("2020-01-01", periods=len(returns), freq="D"),
    )

    # --- quantstats: production-grade performance metrics ---
    try:
        sharpe = round(_safe_float(qs.stats.sharpe(ret_series)), 4) if trade_count > 1 else 0.0
    except Exception:
        sharpe = 0.0
    try:
        sortino = round(_safe_float(qs.stats.sortino(ret_series)), 4) if trade_count > 1 else 0.0
    except Exception:
        sortino = 0.0
    try:
        max_drawdown = round(abs(_safe_float(qs.stats.max_drawdown(ret_series))), 4)
    except Exception:
        max_drawdown = 0.0
    try:
        calmar = round(_safe_float(qs.stats.calmar(ret_series)), 4) if max_drawdown > 0 and trade_count > 1 else 0.0
    except Exception:
        calmar = 0.0

    # Clamp extreme values from small samples
    sharpe = max(-10.0, min(10.0, sharpe))
    sortino = max(-10.0, min(10.0, sortino))
    calmar = max(-100.0, min(100.0, calmar))

    # --- quantstats additional metrics ---
    try:
        profit_factor = round(_safe_float(qs.stats.profit_factor(ret_series)), 4)
    except Exception:
        gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
        profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else 999.0

    try:
        avg_win = round(_safe_float(qs.stats.avg_win(ret_series)), 6)
    except Exception:
        avg_win = round(float(np.mean(wins)), 6) if len(wins) > 0 else 0.0
    try:
        avg_loss = round(_safe_float(qs.stats.avg_loss(ret_series)), 6)
    except Exception:
        avg_loss = round(float(np.mean(losses)), 6) if len(losses) > 0 else 0.0
    try:
        payoff_ratio = round(_safe_float(qs.stats.payoff_ratio(ret_series)), 4)
    except Exception:
        payoff_ratio = round(avg_win / abs(avg_loss), 4) if avg_loss != 0 else 999.0

    # VaR / CVaR (new — not available in empyrical)
    try:
        value_at_risk = round(_safe_float(qs.stats.value_at_risk(ret_series)), 6)
    except Exception:
        value_at_risk = 0.0
    try:
        cvar = round(_safe_float(qs.stats.conditional_value_at_risk(ret_series)), 6)
    except Exception:
        cvar = 0.0

    # Expectancy
    loss_rate = 1 - win_rate
    expectancy = round((win_rate * avg_win) - (loss_rate * abs(avg_loss)), 6)

    # Drift detection — rolling Sharpe vs backtest baseline
    statistics_computations_total.inc()
    sid = payload.strategy_id or "default"
    recent_window = 20
    recent_sharpe_val: float | None = None

    if trade_count >= recent_window and payload.baseline_sharpe is not None:
        recent_returns = returns[-recent_window:]
        try:
            recent_ret_series = pd.Series(
                recent_returns,
                index=pd.date_range("2020-01-01", periods=len(recent_returns), freq="D"),
            )
            recent_sharpe_val = _safe_float(qs.stats.sharpe(recent_ret_series)) if len(recent_returns) > 1 else 0.0
        except Exception:
            recent_sharpe_val = 0.0
        recent_sharpe_val = max(-10.0, min(10.0, recent_sharpe_val))

        drift_score = abs(recent_sharpe_val - payload.baseline_sharpe)
        drift_pct = drift_score / max(abs(payload.baseline_sharpe), 0.1)

        if drift_pct > 0.5:  # >50% degradation
            alert_level = 2  # red
        elif drift_pct > 0.25:  # >25% degradation
            alert_level = 1  # yellow
        else:
            alert_level = 0  # green

        drift_detected = alert_level >= 1
    else:
        # Fallback to simple expected_return check
        drift_score = abs(total_return - payload.expected_return)
        if drift_score > 0.1:
            alert_level = 2
        elif drift_score > 0.05:
            alert_level = 1
        else:
            alert_level = 0
        drift_detected = total_return < payload.expected_return

    # Update drift gauges
    strategy_drift_score.labels(strategy_id=sid).set(round(drift_score, 4))
    strategy_drift_alert.labels(strategy_id=sid).set(alert_level)
    drift_alert_threshold.labels(strategy_id=sid).set(0.25 if payload.baseline_sharpe is not None else 0.05)

    # Publish drift alert event when alert_level >= 1
    if alert_level >= 1 and _drift_event_callback is not None:
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
                        "alert_level": alert_level,
                        "recent_sharpe": recent_sharpe_val,
                        "baseline_sharpe": payload.baseline_sharpe,
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
        drift_detected=drift_detected,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        calmar_ratio=calmar,
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff_ratio=payoff_ratio,
        expectancy=expectancy,
        value_at_risk=value_at_risk,
        conditional_value_at_risk=cvar,
        drift_score=round(drift_score, 4),
        recent_sharpe=round(recent_sharpe_val, 4) if recent_sharpe_val is not None else None,
        recent_trade_pnls=payload.trade_pnls[-10:],
    )
