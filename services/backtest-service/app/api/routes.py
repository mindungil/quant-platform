from fastapi import APIRouter, Header, HTTPException
from app.core import evaluator
from app.models.backtest import BacktestJob, BacktestRequest, BacktestResult

router = APIRouter()


@router.post("/backtests/run", response_model=BacktestJob)
async def run_backtest(
    payload: BacktestRequest,
    x_user_id: str | None = Header(default=None),
) -> BacktestJob:
    """Submit a backtest job. Returns immediately with a job_id to poll."""
    return evaluator.submit_job(payload, user_id=x_user_id or "system")


@router.get("/backtests/{job_id}", response_model=BacktestJob)
def get_backtest(job_id: str, x_user_id: str | None = Header(default=None)) -> BacktestJob:
    """Poll for backtest job status and results."""
    job = evaluator.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if x_user_id is not None and job.user_id != x_user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return job


@router.post("/backtests/backtrader")
def run_backtrader(payload: dict) -> dict:
    """Run a backtest using the Backtrader engine."""
    from app.core.bt_engine import run_backtrader_backtest, BACKTRADER_AVAILABLE

    if not BACKTRADER_AVAILABLE:
        raise HTTPException(status_code=503, detail="backtrader not available")

    from app.core.evaluator import _fetch_candles, _calc_rsi, _calc_macd

    import numpy as np
    import pandas as pd

    asset = payload.get("asset", "BTCUSDT")
    sample_size = payload.get("sample_size", 200)
    weights = payload.get("weights", {"RSI": 1.0})

    df = _fetch_candles(asset, sample_size)
    closes = df["close"]

    indicators = {}
    indicators["rsi"] = (_calc_rsi(closes) - 50) / 50
    indicators["macd"] = _calc_macd(closes)

    score = pd.Series(0.0, index=df.index)
    for name, weight in weights.items():
        key = name.lower()
        if key in indicators:
            score += indicators[key] * weight
        else:
            score += indicators["rsi"] * weight

    from app.core.config import settings

    result = run_backtrader_backtest(
        df=df,
        scores=np.array(score),
        commission_pct=(settings.slippage_bps + settings.commission_bps) / 10000,
        entry_threshold=settings.entry_threshold,
        exit_threshold=settings.exit_threshold,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
        trailing_stop_pct=settings.trailing_stop_pct,
    )
    return result


@router.post("/backtests/monte-carlo")
def run_monte_carlo_simulation(payload: dict):
    """Run Monte Carlo simulation on a strategy's trade returns."""
    from app.core.monte_carlo import run_monte_carlo

    returns = payload.get("trade_returns", [])
    n = payload.get("simulations", 1000)
    confidence = payload.get("confidence_level", 0.95)
    result = run_monte_carlo(returns, n_simulations=n, confidence_level=confidence)
    return result


@router.post("/backtests/nautilus")
def run_nautilus(payload: dict):
    """Replay decisions through Nautilus Trader's HFT-grade engine.

    Models fees + slippage realistically (production-quality estimate).
    """
    from app.core.nautilus_runner import run_nautilus_backtest

    decisions = payload.get("decisions") or []
    candles = payload.get("candles") or []
    starting_balance = payload.get("starting_balance", 10000.0)
    fee_bps = payload.get("fee_bps", 10.0)
    slippage_bps = payload.get("slippage_bps", 5.0)
    return run_nautilus_backtest(
        decisions, candles,
        starting_balance=starting_balance,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
