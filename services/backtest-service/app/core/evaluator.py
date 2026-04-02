import asyncio
import logging
import math
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd
import empyrical as ep

from app.core.config import settings
from app.models.backtest import BacktestJob, BacktestRequest, BacktestResult

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Synthetic price generator (fallback when market-data unavailable)
# ---------------------------------------------------------------------------


def _generate_synthetic_candles(n: int, seed: int = 42) -> pd.DataFrame:
    """Generate realistic-looking BTC hourly candles via geometric Brownian motion."""
    rng = np.random.default_rng(seed)
    dt = 1 / (365 * 24)  # hourly steps
    mu, sigma = 0.0, 0.6  # annualised drift and vol
    log_returns = (mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * rng.standard_normal(n)
    prices = 30000 * np.exp(np.cumsum(log_returns))  # start around 30k
    high = prices * (1 + rng.uniform(0, 0.005, n))
    low = prices * (1 - rng.uniform(0, 0.005, n))
    open_ = prices * (1 + rng.uniform(-0.002, 0.002, n))
    volume = rng.uniform(100, 2000, n)
    ts = pd.date_range(end=datetime.now(UTC), periods=n, freq="h")
    return pd.DataFrame(
        {"timestamp": ts, "open": open_, "high": high, "low": low, "close": prices, "volume": volume}
    )


# ---------------------------------------------------------------------------
# Fetch candles from market-data service
# ---------------------------------------------------------------------------


def _fetch_candles(asset: str, limit: int) -> pd.DataFrame:
    """Try to fetch historical candles from market-data; fall back to synthetic."""
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
            logger.warning("market-data returned only %d candles, using synthetic data", len(df))
    except Exception as exc:
        logger.warning("could not fetch candles from market-data (%s), using synthetic data", exc)
    return _generate_synthetic_candles(limit)


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------


def _simulate_trades(
    closes: pd.Series,
    score: pd.Series,
    start: int,
    end: int,
    entry_threshold: float,
    exit_threshold: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
    cost_per_trade: float,
) -> tuple[list[dict], float]:
    """Run trade simulation on a slice. Returns (trades, total_commission)."""
    trades = []
    total_commission = 0.0
    position = None

    for i in range(max(start, 1), end):
        price = closes.iloc[i]
        s = score.iloc[i]

        if position is None:
            if s > entry_threshold:
                effective_entry = price * (1 + cost_per_trade)
                total_commission += price * cost_per_trade
                position = {"side": "BUY", "entry_price": effective_entry, "highest": price, "entry_idx": i}
            elif s < -entry_threshold:
                effective_entry = price * (1 - cost_per_trade)
                total_commission += price * cost_per_trade
                position = {"side": "SELL", "entry_price": effective_entry, "lowest": price, "entry_idx": i}
        else:
            entry_price = position["entry_price"]

            if position["side"] == "BUY":
                position["highest"] = max(position.get("highest", price), price)
                effective_exit = price * (1 - cost_per_trade)
                pnl_pct = (effective_exit - entry_price) / entry_price

                should_exit = False
                exit_reason = ""
                if pnl_pct >= take_profit_pct:
                    should_exit, exit_reason = True, "take_profit"
                elif pnl_pct <= -stop_loss_pct:
                    should_exit, exit_reason = True, "stop_loss"
                elif trailing_stop_pct > 0 and price <= position["highest"] * (1 - trailing_stop_pct):
                    should_exit, exit_reason = True, "trailing_stop"
                elif s < -exit_threshold:
                    should_exit, exit_reason = True, "signal_reversal"

                if should_exit:
                    total_commission += price * cost_per_trade
                    trades.append({"entry_price": entry_price, "exit_price": effective_exit, "pnl_pct": pnl_pct, "side": "BUY", "reason": exit_reason})
                    position = None

            else:  # SELL (short)
                position["lowest"] = min(position.get("lowest", price), price)
                effective_exit = price * (1 + cost_per_trade)
                pnl_pct = (entry_price - effective_exit) / entry_price

                should_exit = False
                exit_reason = ""
                if pnl_pct >= take_profit_pct:
                    should_exit, exit_reason = True, "take_profit"
                elif pnl_pct <= -stop_loss_pct:
                    should_exit, exit_reason = True, "stop_loss"
                elif trailing_stop_pct > 0 and price >= position["lowest"] * (1 + trailing_stop_pct):
                    should_exit, exit_reason = True, "trailing_stop"
                elif s > exit_threshold:
                    should_exit, exit_reason = True, "signal_reversal"

                if should_exit:
                    total_commission += price * cost_per_trade
                    trades.append({"entry_price": entry_price, "exit_price": effective_exit, "pnl_pct": pnl_pct, "side": "SELL", "reason": exit_reason})
                    position = None

    # Close open position at end
    if position is not None:
        price = closes.iloc[end - 1]
        entry_price = position["entry_price"]
        cost = price * cost_per_trade
        total_commission += cost
        if position["side"] == "BUY":
            effective_exit = price * (1 - cost_per_trade)
            pnl_pct = (effective_exit - entry_price) / entry_price
        else:
            effective_exit = price * (1 + cost_per_trade)
            pnl_pct = (entry_price - effective_exit) / entry_price
        trades.append({"entry_price": entry_price, "exit_price": effective_exit, "pnl_pct": pnl_pct, "side": position["side"], "reason": "end_of_data"})

    return trades, total_commission


def _calc_metrics(trades: list[dict], risk_free_daily: float) -> dict:
    """Calculate all performance metrics from a list of trades."""
    if not trades:
        return {"sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0, "win_rate": 0.0,
                "total_return": 0.0, "profit_factor": 0.0, "calmar": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "payoff_ratio": 0.0, "mean_pnl": 0.0}

    returns = np.array([t["pnl_pct"] for t in trades])
    n = len(returns)
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    win_rate = float(len(wins) / n)
    mean_ret = float(np.mean(returns))

    # Sharpe (empyrical - annualized, risk-free adjusted)
    sharpe = float(ep.sharpe_ratio(returns, risk_free=risk_free_daily))

    # Sortino (empyrical)
    sortino = float(ep.sortino_ratio(returns, required_return=risk_free_daily))

    # Max drawdown (empyrical)
    max_dd = float(abs(ep.max_drawdown(returns)))

    # Profit factor
    gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

    # Calmar
    ann_return = mean_ret * 252
    calmar = ann_return / max_dd if max_dd > 0 else 0.0

    # Win/loss averages
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    payoff_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 999.0

    total_return = float(np.prod(1 + returns) - 1)

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
    }


def evaluate_strategy(payload: BacktestRequest) -> BacktestResult:
    """Run backtest with transaction costs and walk-forward validation."""
    df = _fetch_candles(payload.asset, payload.sample_size)
    closes = df["close"]

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

    cost_per_trade = (settings.slippage_bps + settings.commission_bps) / 10000
    risk_free_daily = (1 + settings.risk_free_rate_annual) ** (1/252) - 1

    # --- Full-period simulation ---
    trades, total_commission = _simulate_trades(
        closes, score, 0, len(df),
        settings.entry_threshold, settings.exit_threshold,
        settings.stop_loss_pct, settings.take_profit_pct, settings.trailing_stop_pct,
        cost_per_trade,
    )

    trade_count = len(trades)
    if trade_count == 0:
        return BacktestResult(
            strategy_id=payload.strategy_id, sharpe_ratio=0.0, sortino_ratio=0.0,
            max_drawdown=0.0, win_rate=0.0, total_return=0.0, trade_count=0,
            avg_trade_pnl=0.0, status="FAILED",
        )

    m = _calc_metrics(trades, risk_free_daily)

    # --- Walk-forward out-of-sample validation ---
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
        oos_trades, _ = _simulate_trades(
            closes, score, split, w_end,
            settings.entry_threshold, settings.exit_threshold,
            settings.stop_loss_pct, settings.take_profit_pct, settings.trailing_stop_pct,
            cost_per_trade,
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


def submit_job(payload: BacktestRequest) -> BacktestJob:
    """Create a job entry and schedule async evaluation. Returns immediately."""
    job_id = str(uuid4())
    job = BacktestJob(job_id=job_id, strategy_id=payload.strategy_id, status="PENDING")
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

        # Publish event if a callback is registered
        if _event_callback is not None:
            try:
                event_data = {
                    "job_id": job_id,
                    "strategy_id": payload.strategy_id,
                    "backtest_status": result.status,
                    "sharpe_ratio": result.sharpe_ratio,
                }
                await _event_callback("backtest.completed", event_data)
            except Exception:
                logger.exception("failed to publish backtest.completed event for job %s", job_id)
    except Exception as exc:
        job.status = "FAILED"
        job.error = str(exc)
        job.completed_at = datetime.now(UTC)
        logger.exception("backtest job %s failed", job_id)
