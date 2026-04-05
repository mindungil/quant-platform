import asyncio
import logging
import math
import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd
# quantstats 0.0.64 uses IPython for HTML reports; mock it so the server starts without Jupyter
import sys as _sys, types as _types
if "IPython" not in _sys.modules:
    _ipy = _types.ModuleType("IPython")
    _ipy.core = _types.ModuleType("IPython.core")
    _ipy.core.display = _types.ModuleType("IPython.core.display")
    _ipy.core.display.display = lambda *a, **k: None
    _ipy.core.display.HTML = type("HTML", (), {"__init__": lambda self, *a, **k: None})
    _ipy.display = _types.ModuleType("IPython.display")
    _ipy.display.display = lambda *a, **k: None
    _ipy.display.HTML = _ipy.core.display.HTML
    for _mod, _obj in [
        ("IPython", _ipy), ("IPython.core", _ipy.core),
        ("IPython.core.display", _ipy.core.display), ("IPython.display", _ipy.display),
    ]:
        _sys.modules[_mod] = _obj
import quantstats as qs

from app.core.config import settings
from app.models.backtest import BacktestJob, BacktestRequest, BacktestResult
from shared.statistics import validate_backtest

logger = logging.getLogger(__name__)


def _safe_float(val, default: float = 0.0) -> float:
    """Convert quantstats result to float, returning *default* for NaN/Inf."""
    try:
        v = float(val)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Indicator calculations
# ---------------------------------------------------------------------------


def _calc_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI (0-100) from a close price series."""
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _calc_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """Compute MACD histogram (normalised by price for cross-asset comparability)."""
    ema_fast = closes.ewm(span=fast, min_periods=fast).mean()
    ema_slow = closes.ewm(span=slow, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal).mean()
    histogram = macd_line - signal_line
    # Normalise to roughly [-1, 1] by dividing by rolling std of close
    norm = closes.rolling(slow).std().replace(0, 1)
    return (histogram / norm).fillna(0)


def _calc_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    prev_close = closes.shift(1)
    tr1 = highs - lows
    tr2 = (highs - prev_close).abs()
    tr3 = (lows - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, min_periods=period).mean()
    return atr.fillna(method="bfill").fillna(tr)


# ---------------------------------------------------------------------------
# Slippage model
# ---------------------------------------------------------------------------


def _calc_slippage(price: float, base_cost_bps: float, order_size: float, avg_volume: float) -> float:
    """Volume-impact slippage model.

    Slippage = base_cost + volume_impact
    volume_impact scales quadratically with order_size / avg_volume ratio,
    reflecting that larger orders relative to volume cause more market impact.
    """
    base = price * (base_cost_bps / 10000)
    if avg_volume > 0 and order_size > 0:
        participation_rate = min(order_size / avg_volume, 1.0)
        # Square-root impact model (common in execution cost literature)
        impact = price * 0.001 * math.sqrt(participation_rate)
    else:
        impact = 0.0
    return base + impact


# ---------------------------------------------------------------------------
# Fetch candles from market-data service (NO synthetic fallback)
# ---------------------------------------------------------------------------


def _fetch_candles(asset: str, limit: int) -> pd.DataFrame:
    """Fetch historical candles from market-data service.

    Raises ValueError if insufficient real data is available.
    """
    try:
        url = f"{settings.market_data_base_url}/candles/{asset}/history"
        resp = httpx.get(url, params={"limit": limit}, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        if data:
            df = pd.DataFrame(data)
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)
            if len(df) >= 50:
                return df
            raise ValueError(
                f"market-data returned only {len(df)} candles for {asset}, "
                f"need at least 50 for a meaningful backtest"
            )
    except httpx.HTTPError as exc:
        raise ValueError(
            f"Failed to fetch candles from market-data for {asset}: {exc}"
        ) from exc
    raise ValueError(f"No candle data returned from market-data for {asset}")


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------


def _simulate_trades(
    closes: pd.Series,
    highs: pd.Series,
    lows: pd.Series,
    volumes: pd.Series,
    atr: pd.Series,
    score: pd.Series,
    start: int,
    end: int,
    entry_threshold: float,
    exit_threshold: float,
    atr_stop_mult: float,
    atr_tp_mult: float,
    atr_trailing_mult: float,
    commission_bps: float,
    slippage_bps: float,
    order_size: float = 1.0,
) -> tuple[list[dict], float]:
    """Run trade simulation on a slice with ATR-based stops and volume-impact slippage.

    Returns (trades, total_commission).
    """
    trades: list[dict] = []
    total_commission = 0.0
    position = None

    # Pre-compute rolling average volume for slippage model
    avg_vol = volumes.rolling(20, min_periods=1).mean()

    for i in range(max(start, 1), end):
        price = closes.iloc[i]
        high = highs.iloc[i]
        low = lows.iloc[i]
        s = score.iloc[i]
        current_atr = atr.iloc[i]
        current_avg_vol = avg_vol.iloc[i]

        if position is None:
            if s > entry_threshold:
                slip = _calc_slippage(price, slippage_bps, order_size, current_avg_vol)
                commission = price * (commission_bps / 10000)
                total_commission += commission + slip
                effective_entry = price + slip + commission
                # ATR-based stops
                stop_price = price - atr_stop_mult * current_atr
                tp_price = price + atr_tp_mult * current_atr
                trailing_stop = price - atr_trailing_mult * current_atr
                position = {
                    "side": "BUY", "entry_price": effective_entry, "raw_entry": price,
                    "highest": high, "entry_idx": i,
                    "stop_price": stop_price, "tp_price": tp_price,
                    "trailing_stop": trailing_stop, "atr_at_entry": current_atr,
                    "max_favorable": 0.0, "max_adverse": 0.0,
                }
            elif s < -entry_threshold:
                slip = _calc_slippage(price, slippage_bps, order_size, current_avg_vol)
                commission = price * (commission_bps / 10000)
                total_commission += commission + slip
                effective_entry = price - slip - commission
                stop_price = price + atr_stop_mult * current_atr
                tp_price = price - atr_tp_mult * current_atr
                trailing_stop = price + atr_trailing_mult * current_atr
                position = {
                    "side": "SELL", "entry_price": effective_entry, "raw_entry": price,
                    "lowest": low, "entry_idx": i,
                    "stop_price": stop_price, "tp_price": tp_price,
                    "trailing_stop": trailing_stop, "atr_at_entry": current_atr,
                    "max_favorable": 0.0, "max_adverse": 0.0,
                }
        else:
            entry_price = position["entry_price"]
            raw_entry = position["raw_entry"]

            if position["side"] == "BUY":
                position["highest"] = max(position.get("highest", high), high)
                # Track MFE / MAE
                unrealized = (high - raw_entry) / raw_entry
                position["max_favorable"] = max(position["max_favorable"], unrealized)
                adverse = (raw_entry - low) / raw_entry
                position["max_adverse"] = max(position["max_adverse"], adverse)

                # Update trailing ATR stop
                new_trailing = position["highest"] - atr_trailing_mult * current_atr
                position["trailing_stop"] = max(position["trailing_stop"], new_trailing)

                # Exit logic
                slip = _calc_slippage(price, slippage_bps, order_size, current_avg_vol)
                commission = price * (commission_bps / 10000)
                effective_exit = price - slip - commission
                pnl_pct = (effective_exit - entry_price) / entry_price

                should_exit = False
                exit_reason = ""
                if high >= position["tp_price"]:
                    should_exit, exit_reason = True, "take_profit"
                    effective_exit = position["tp_price"] - slip - commission
                    pnl_pct = (effective_exit - entry_price) / entry_price
                elif low <= position["stop_price"]:
                    should_exit, exit_reason = True, "stop_loss"
                    effective_exit = position["stop_price"] - slip - commission
                    pnl_pct = (effective_exit - entry_price) / entry_price
                elif low <= position["trailing_stop"]:
                    should_exit, exit_reason = True, "trailing_stop"
                    effective_exit = position["trailing_stop"] - slip - commission
                    pnl_pct = (effective_exit - entry_price) / entry_price
                elif s < -exit_threshold:
                    should_exit, exit_reason = True, "signal_reversal"

                if should_exit:
                    total_commission += commission + slip
                    trades.append({
                        "entry_price": entry_price, "exit_price": effective_exit,
                        "pnl_pct": pnl_pct, "side": "BUY", "reason": exit_reason,
                        "max_favorable_excursion": position["max_favorable"],
                        "max_adverse_excursion": position["max_adverse"],
                    })
                    position = None

            else:  # SELL (short)
                position["lowest"] = min(position.get("lowest", low), low)
                unrealized = (raw_entry - low) / raw_entry
                position["max_favorable"] = max(position["max_favorable"], unrealized)
                adverse = (high - raw_entry) / raw_entry
                position["max_adverse"] = max(position["max_adverse"], adverse)

                # Update trailing ATR stop (moves down for shorts)
                new_trailing = position["lowest"] + atr_trailing_mult * current_atr
                position["trailing_stop"] = min(position["trailing_stop"], new_trailing)

                slip = _calc_slippage(price, slippage_bps, order_size, current_avg_vol)
                commission = price * (commission_bps / 10000)
                effective_exit = price + slip + commission
                pnl_pct = (entry_price - effective_exit) / entry_price

                should_exit = False
                exit_reason = ""
                if low <= position["tp_price"]:
                    should_exit, exit_reason = True, "take_profit"
                    effective_exit = position["tp_price"] + slip + commission
                    pnl_pct = (entry_price - effective_exit) / entry_price
                elif high >= position["stop_price"]:
                    should_exit, exit_reason = True, "stop_loss"
                    effective_exit = position["stop_price"] + slip + commission
                    pnl_pct = (entry_price - effective_exit) / entry_price
                elif high >= position["trailing_stop"]:
                    should_exit, exit_reason = True, "trailing_stop"
                    effective_exit = position["trailing_stop"] + slip + commission
                    pnl_pct = (entry_price - effective_exit) / entry_price
                elif s > exit_threshold:
                    should_exit, exit_reason = True, "signal_reversal"

                if should_exit:
                    total_commission += commission + slip
                    trades.append({
                        "entry_price": entry_price, "exit_price": effective_exit,
                        "pnl_pct": pnl_pct, "side": "SELL", "reason": exit_reason,
                        "max_favorable_excursion": position["max_favorable"],
                        "max_adverse_excursion": position["max_adverse"],
                    })
                    position = None

    # Close open position at end
    if position is not None:
        price = closes.iloc[end - 1]
        entry_price = position["entry_price"]
        avg_v = avg_vol.iloc[end - 1]
        slip = _calc_slippage(price, slippage_bps, order_size, avg_v)
        commission = price * (commission_bps / 10000)
        total_commission += commission + slip
        if position["side"] == "BUY":
            effective_exit = price - slip - commission
            pnl_pct = (effective_exit - entry_price) / entry_price
        else:
            effective_exit = price + slip + commission
            pnl_pct = (entry_price - effective_exit) / entry_price
        trades.append({
            "entry_price": entry_price, "exit_price": effective_exit,
            "pnl_pct": pnl_pct, "side": position["side"], "reason": "end_of_data",
            "max_favorable_excursion": position.get("max_favorable", 0.0),
            "max_adverse_excursion": position.get("max_adverse", 0.0),
        })

    return trades, total_commission


def _calc_metrics(trades: list[dict], risk_free_daily: float) -> dict:
    """Calculate all performance metrics from a list of trades."""
    if not trades:
        return {
            "sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0, "win_rate": 0.0,
            "total_return": 0.0, "profit_factor": 0.0, "calmar": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "payoff_ratio": 0.0, "mean_pnl": 0.0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            "max_favorable_excursion": 0.0, "max_adverse_excursion": 0.0,
        }

    returns = np.array([t["pnl_pct"] for t in trades])
    n = len(returns)
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    win_rate = float(len(wins) / n)
    mean_ret = float(np.mean(returns))

    # Build a dated Series for quantstats (requires DatetimeIndex)
    ret_series = pd.Series(
        returns,
        index=pd.date_range("2020-01-01", periods=len(returns), freq="D"),
    )

    # Sharpe (quantstats - annualized)
    try:
        sharpe = _safe_float(qs.stats.sharpe(ret_series))
    except Exception:
        sharpe = 0.0

    # Sortino (quantstats)
    try:
        sortino = _safe_float(qs.stats.sortino(ret_series))
    except Exception:
        sortino = 0.0

    # Max drawdown (quantstats)
    try:
        max_dd = abs(_safe_float(qs.stats.max_drawdown(ret_series)))
    except Exception:
        max_dd = 0.0

    # Profit factor (quantstats)
    try:
        profit_factor = _safe_float(qs.stats.profit_factor(ret_series))
    except Exception:
        gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

    # Calmar (quantstats)
    try:
        calmar = _safe_float(qs.stats.calmar(ret_series))
    except Exception:
        ann_return = mean_ret * 252
        calmar = ann_return / max_dd if max_dd > 0 else 0.0

    # Win/loss averages (quantstats)
    try:
        avg_win = _safe_float(qs.stats.avg_win(ret_series))
    except Exception:
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    try:
        avg_loss = _safe_float(qs.stats.avg_loss(ret_series))
    except Exception:
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    try:
        payoff_ratio = _safe_float(qs.stats.payoff_ratio(ret_series))
    except Exception:
        payoff_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 999.0

    total_return = float(np.prod(1 + returns) - 1)

    # Consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    current_wins = 0
    current_losses = 0
    for r in returns:
        if r > 0:
            current_wins += 1
            current_losses = 0
            max_consec_wins = max(max_consec_wins, current_wins)
        elif r < 0:
            current_losses += 1
            current_wins = 0
            max_consec_losses = max(max_consec_losses, current_losses)
        else:
            current_wins = 0
            current_losses = 0

    # Max favorable / adverse excursion across all trades
    mfe = max((t.get("max_favorable_excursion", 0.0) for t in trades), default=0.0)
    mae = max((t.get("max_adverse_excursion", 0.0) for t in trades), default=0.0)

    # Clamp ratios to prevent extreme values from tiny samples
    sharpe = max(-10.0, min(10.0, sharpe))
    sortino = max(-10.0, min(10.0, sortino))
    calmar = max(-100.0, min(100.0, calmar))

    return {
        "sharpe": round(sharpe, 4), "sortino": round(sortino, 4),
        "max_dd": round(max_dd, 4), "win_rate": round(win_rate, 4),
        "total_return": round(total_return, 4), "profit_factor": round(profit_factor, 4),
        "calmar": round(calmar, 4), "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6), "payoff_ratio": round(payoff_ratio, 4),
        "mean_pnl": round(mean_ret, 6),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "max_favorable_excursion": round(mfe, 6),
        "max_adverse_excursion": round(mae, 6),
    }


def evaluate_strategy(payload: BacktestRequest) -> BacktestResult:
    """Run backtest with ATR-based stops, volume-impact slippage, and walk-forward validation.

    Raises ValueError if real market data is unavailable.
    """
    df = _fetch_candles(payload.asset, payload.sample_size)
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]
    volumes = df["volume"]

    # Compute indicators and weighted signal score
    indicators: dict[str, pd.Series] = {}
    indicators["rsi"] = (_calc_rsi(closes) - 50) / 50
    indicators["macd"] = _calc_macd(closes)

    score = pd.Series(0.0, index=df.index)
    for name, weight in payload.weights.items():
        key = name.lower()
        if key in indicators:
            score += indicators[key] * weight
        else:
            score += indicators["rsi"] * weight

    # Compute ATR for adaptive stops
    atr = _calc_atr(highs, lows, closes)

    commission_bps = settings.commission_bps
    slippage_bps = settings.slippage_bps
    risk_free_daily = (1 + settings.risk_free_rate_annual) ** (1/252) - 1

    # ATR multipliers for stops
    atr_stop_mult = 2.0
    atr_tp_mult = 3.0
    atr_trailing_mult = 2.5

    # --- Full-period simulation ---
    trades, total_commission = _simulate_trades(
        closes, highs, lows, volumes, atr, score,
        0, len(df),
        settings.entry_threshold, settings.exit_threshold,
        atr_stop_mult, atr_tp_mult, atr_trailing_mult,
        commission_bps, slippage_bps,
    )

    trade_count = len(trades)
    if trade_count == 0:
        return BacktestResult(
            strategy_id=payload.strategy_id, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, total_return=0.0, trade_count=0,
            avg_trade_pnl=0.0, status="FAILED",
        )

    m = _calc_metrics(trades, risk_free_daily)

    # --- Walk-forward out-of-sample validation with adaptive thresholds ---
    n = len(df)
    n_windows = settings.walk_forward_windows
    window_size = n // n_windows if n_windows > 0 else n
    oos_sharpes = []
    for w in range(n_windows):
        w_start = w * window_size
        w_end = min((w + 1) * window_size, n)
        split = int(w_start + (w_end - w_start) * settings.train_ratio)
        if split >= w_end - 5:
            continue

        # Adaptive thresholds: optimize entry/exit on the training window
        train_scores = score.iloc[w_start:split]
        if len(train_scores) > 10:
            score_std = train_scores.std()
            adaptive_entry = max(0.1, score_std * 1.5)
            adaptive_exit = max(0.05, score_std * 0.8)
        else:
            adaptive_entry = settings.entry_threshold
            adaptive_exit = settings.exit_threshold

        oos_trades, _ = _simulate_trades(
            closes, highs, lows, volumes, atr, score,
            split, w_end,
            adaptive_entry, adaptive_exit,
            atr_stop_mult, atr_tp_mult, atr_trailing_mult,
            commission_bps, slippage_bps,
        )
        if len(oos_trades) >= 3:  # need minimum trades for meaningful Sharpe
            oos_m = _calc_metrics(oos_trades, risk_free_daily)
            # Clamp to reasonable range to avoid extreme values from tiny samples
            clamped = max(-10.0, min(10.0, oos_m["sharpe"]))
            oos_sharpes.append(clamped)
    oos_sharpe = round(sum(oos_sharpes) / len(oos_sharpes), 4) if oos_sharpes else 0.0

    # Pass/fail with realistic criteria (post-cost)
    status = "PASSED" if (
        m["sharpe"] >= 0.8
        and m["max_dd"] <= 0.20
        and m["win_rate"] >= 0.45
        and trade_count >= 10
        and m["profit_factor"] >= 1.2
    ) else "FAILED"

    # --- Statistical validation ---
    trade_returns = [t["pnl_pct"] for t in trades]
    # Use buy-and-hold of the asset as a simple benchmark
    daily_returns = closes.pct_change().dropna().tolist()
    try:
        stat_validation = validate_backtest(trade_returns, daily_returns)
    except Exception:
        logger.exception("statistical validation failed, continuing without it")
        stat_validation = {}

    return BacktestResult(
        strategy_id=payload.strategy_id,
        sharpe_ratio=m["sharpe"],
        sortino_ratio=m["sortino"],
        max_drawdown=m["max_dd"],
        win_rate=m["win_rate"],
        total_return=m["total_return"],
        trade_count=trade_count,
        avg_trade_pnl=m["mean_pnl"],
        profit_factor=m["profit_factor"],
        calmar_ratio=m["calmar"],
        avg_win=m["avg_win"],
        avg_loss=m["avg_loss"],
        payoff_ratio=m["payoff_ratio"],
        total_commission=round(total_commission, 2),
        out_of_sample_sharpe=oos_sharpe,
        max_consecutive_wins=m["max_consecutive_wins"],
        max_consecutive_losses=m["max_consecutive_losses"],
        max_favorable_excursion=m["max_favorable_excursion"],
        max_adverse_excursion=m["max_adverse_excursion"],
        statistical_validation=stat_validation,
        status=status,
    )


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

_jobs: dict[str, BacktestJob] = {}

# Optional callback for publishing events (set by routes at startup)
_event_callback = None


def set_event_callback(cb):  # noqa: ANN001
    global _event_callback
    _event_callback = cb


def get_job(job_id: str) -> BacktestJob | None:
    return _jobs.get(job_id)


def submit_job(payload: BacktestRequest, user_id: str = "system") -> BacktestJob:
    """Create a job entry and schedule async evaluation. Returns immediately."""
    job_id = str(uuid4())
    job = BacktestJob(job_id=job_id, strategy_id=payload.strategy_id, user_id=user_id, status="PENDING")
    _jobs[job_id] = job

    # Fire-and-forget the actual evaluation on the running event loop
    asyncio.get_event_loop().create_task(_run_job(job_id, payload))
    return job


async def _run_job(job_id: str, payload: BacktestRequest) -> None:
    job = _jobs[job_id]
    job.status = "RUNNING"
    try:
        # Run the (synchronous) evaluation in a thread to avoid blocking
        result = await asyncio.get_event_loop().run_in_executor(None, evaluate_strategy, payload)
        job.result = result
        job.status = "COMPLETED"
        job.completed_at = datetime.now(UTC)
        logger.info("backtest job %s completed: %s", job_id, result.status)

        # Publish backtest.completed event with full metrics
        if _event_callback is not None:
            try:
                event_data = {
                    "job_id": job_id,
                    "strategy_id": payload.strategy_id,
                    "backtest_status": result.status,
                    "sharpe_ratio": result.sharpe_ratio,
                    "total_return": result.total_return,
                    "max_drawdown": result.max_drawdown,
                    "win_rate": result.win_rate,
                }
                await _event_callback("backtest.completed", event_data)
            except Exception:
                logger.exception("failed to publish backtest.completed event for job %s", job_id)

        # Auto-promote strategy based on backtest results
        if payload.strategy_id:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    registry_url = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
                    if result.sharpe_ratio > 0.5:
                        new_status = "TESTED"
                    elif result.sharpe_ratio < 0:
                        new_status = "ARCHIVED"
                    else:
                        new_status = None
                    if new_status:
                        resp = await client.patch(
                            f"{registry_url}/strategies/{payload.strategy_id}/status",
                            json={"status": new_status},
                        )
                        if resp.status_code == 200:
                            logger.info(
                                "strategy_auto_transitioned",
                                extra={
                                    "strategy_id": payload.strategy_id,
                                    "new_status": new_status,
                                    "sharpe": result.sharpe_ratio,
                                },
                            )
                        else:
                            logger.debug(
                                "strategy_auto_transition_skipped",
                                extra={"status_code": resp.status_code, "detail": resp.text[:200]},
                            )

                    # Store Kelly params in strategy for live trading
                    if result.win_rate and result.win_rate > 0:
                        kelly_params = {
                            "backtest_win_rate": result.win_rate,
                            "backtest_payoff_ratio": result.payoff_ratio or 1.5,
                            "backtest_sharpe": result.sharpe_ratio,
                            "backtest_avg_win": result.avg_win,
                            "backtest_avg_loss": result.avg_loss,
                            "kelly_params_updated_at": datetime.now(UTC).isoformat(),
                        }
                        try:
                            await client.patch(
                                f"{registry_url}/strategies/{payload.strategy_id}/kelly-params",
                                json=kelly_params,
                            )
                            logger.info(
                                "kelly_params_stored",
                                extra={
                                    "strategy_id": payload.strategy_id,
                                    "win_rate": result.win_rate,
                                    "payoff_ratio": result.payoff_ratio,
                                },
                            )
                        except Exception as exc:
                            logger.warning("kelly_params_store_failed", extra={"error": str(exc)[:200]})
            except Exception as exc:
                logger.warning("strategy_auto_promotion_failed", extra={"error": str(exc)[:200]})
    except ValueError as exc:
        # Data unavailability — not an unexpected crash
        job.status = "FAILED"
        job.error = str(exc)
        job.completed_at = datetime.now(UTC)
        logger.warning("backtest job %s failed (data error): %s", job_id, exc)
    except Exception as exc:
        job.status = "FAILED"
        job.error = str(exc)
        job.completed_at = datetime.now(UTC)
        logger.exception("backtest job %s failed", job_id)
