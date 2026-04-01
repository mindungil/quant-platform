import asyncio
import logging
import math
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd

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


def evaluate_strategy(payload: BacktestRequest) -> BacktestResult:
    """Run a real backtest: fetch candles, compute indicators, simulate trades, report metrics."""
    df = _fetch_candles(payload.asset, payload.sample_size)
    closes = df["close"]

    # --- Compute indicators and weighted signal score ---
    indicators: dict[str, pd.Series] = {}
    weight_keys = {k.lower() for k in payload.weights}

    # Always compute RSI and MACD; additional indicators map to RSI by default
    indicators["rsi"] = (_calc_rsi(closes) - 50) / 50  # normalise to ~[-1, 1]
    indicators["macd"] = _calc_macd(closes)

    # Build per-bar score = sum(indicator * weight)
    score = pd.Series(0.0, index=df.index)
    for name, weight in payload.weights.items():
        key = name.lower()
        if key in indicators:
            score += indicators[key] * weight
        else:
            # Unknown indicator — use RSI as proxy
            score += indicators["rsi"] * weight

    # --- Simulate trades ---
    entry_threshold = settings.entry_threshold
    exit_threshold = settings.exit_threshold
    stop_loss_pct = settings.stop_loss_pct

    trades: list[dict] = []  # each: {entry_price, exit_price, pnl_pct, side}
    position: dict | None = None  # None = flat

    for i in range(1, len(df)):
        price = closes.iloc[i]
        s = score.iloc[i]

        if position is None:
            # Enter long
            if s > entry_threshold:
                position = {"side": "BUY", "entry_price": price, "entry_idx": i}
            # Enter short
            elif s < -entry_threshold:
                position = {"side": "SELL", "entry_price": price, "entry_idx": i}
        else:
            entry_price = position["entry_price"]
            if position["side"] == "BUY":
                pnl_pct = (price - entry_price) / entry_price
                # Exit on opposite signal or stop-loss
                if s < -exit_threshold or pnl_pct <= -stop_loss_pct:
                    trades.append({"entry_price": entry_price, "exit_price": price, "pnl_pct": pnl_pct, "side": "BUY"})
                    position = None
            else:  # SELL (short)
                pnl_pct = (entry_price - price) / entry_price
                if s > exit_threshold or pnl_pct <= -stop_loss_pct:
                    trades.append({"entry_price": entry_price, "exit_price": price, "pnl_pct": pnl_pct, "side": "SELL"})
                    position = None

    # Close any open position at last price
    if position is not None:
        price = closes.iloc[-1]
        entry_price = position["entry_price"]
        if position["side"] == "BUY":
            pnl_pct = (price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - price) / entry_price
        trades.append({"entry_price": entry_price, "exit_price": price, "pnl_pct": pnl_pct, "side": position["side"]})

    # --- Calculate metrics ---
    trade_count = len(trades)
    if trade_count == 0:
        return BacktestResult(
            strategy_id=payload.strategy_id,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            total_return=0.0,
            trade_count=0,
            avg_trade_pnl=0.0,
            status="FAILED",
        )

    returns = np.array([t["pnl_pct"] for t in trades])
    wins = np.sum(returns > 0)
    win_rate = round(float(wins / trade_count), 4)

    mean_ret = float(np.mean(returns))
    std_ret = float(np.std(returns, ddof=1)) if trade_count > 1 else 1e-9
    downside = returns[returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1e-9

    sharpe = round(mean_ret / max(std_ret, 1e-9) * math.sqrt(252), 4)
    sortino = round(mean_ret / max(downside_std, 1e-9) * math.sqrt(252), 4)

    # Compounded equity curve for max drawdown
    equity = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (running_max - equity) / running_max
    max_drawdown = round(float(np.max(drawdowns)), 4)

    total_return = round(float(equity[-1] - 1), 4)
    avg_trade_pnl = round(float(mean_ret), 6)

    status = "PASSED" if sharpe >= 1.1 and max_drawdown <= 0.15 and win_rate >= 0.52 else "FAILED"

    return BacktestResult(
        strategy_id=payload.strategy_id,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        total_return=total_return,
        trade_count=trade_count,
        avg_trade_pnl=avg_trade_pnl,
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
