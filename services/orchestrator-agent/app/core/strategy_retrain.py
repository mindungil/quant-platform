"""Strategy auto-retrain & demotion loop.

Periodically:
  1. Lists ACTIVE strategies from the strategy-registry
  2. For each, runs a fresh walk-forward backtest on the most recent
     N bars of real OHLCV data (data/ohlcv/ via shared.alpha + shared.backtest)
  3. If OOS Sharpe degrades below `degradation_threshold`, demotes the
     strategy to PAUSED via PATCH /strategies/{id}/status
  4. If OOS Sharpe recovers above `reactivation_threshold` for a paused
     strategy, reactivates it

This closes the live-monitoring loop. Without it, an alpha that decays
in real markets would silently bleed capital. With it, the platform
gracefully demotes degraded strategies.

Pure-Python; no external deps beyond httpx (already present in this
service) and shared.* modules.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
logger = logging.getLogger("strategy-retrain")


REGISTRY_BASE = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
DATA_DIR = Path(os.getenv("QUANT_DATA_DIR", "/home/ubuntu/quant/data/ohlcv"))
RETRAIN_INTERVAL_SECONDS = int(os.getenv("RETRAIN_INTERVAL_SECONDS", str(6 * 3600)))  # 6h
DEGRADATION_THRESHOLD = float(os.getenv("RETRAIN_DEGRADATION_THRESHOLD", "0.20"))   # OOS sharpe
REACTIVATION_THRESHOLD = float(os.getenv("RETRAIN_REACTIVATION_THRESHOLD", "0.80"))  # OOS sharpe
LOOKBACK_BARS = int(os.getenv("RETRAIN_LOOKBACK_BARS", "4500"))


def _run_alpha_gate() -> dict | None:
    """Best-effort alpha_gate evaluation for the meta-ensemble's live config.

    Returns gate report dict or None if alpha_gate isn't importable (e.g.,
    shared.alpha not on this container's path). Non-blocking by design.
    """
    gate_asset = os.getenv("ALPHA_GATE_ASSET", "ETHUSDT")
    gate_alphas = os.getenv(
        "ALPHA_GATE_ALPHAS", "momentum_ensemble,range_reversion,vol_breakout"
    ).split(",")
    gate_execution = os.getenv("ALPHA_GATE_EXECUTION", "maker")
    try:
        from scripts.alpha_gate import evaluate
        return evaluate(
            gate_asset,
            [a.strip() for a in gate_alphas],
            gate_execution,
            folds=6,
            min_dsr_verdict="marginal",
            max_pbo=0.30,
        )
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("alpha_gate_evaluate_error: %s", str(exc)[:100])
        return None


def _load_recent_ohlcv(symbol: str = "BTCUSDT", interval: str = "1h"):
    """Best-effort load of recent OHLCV. Returns None if unavailable."""
    if not DATA_DIR.exists():
        return None
    try:
        import pandas as pd
        p = DATA_DIR / f"{symbol}_{interval}.parquet"
        c = DATA_DIR / f"{symbol}_{interval}.csv"
        if p.exists():
            df = pd.read_parquet(p)
        elif c.exists():
            df = pd.read_csv(c, index_col=0, parse_dates=True)
        else:
            return None
        if len(df) > LOOKBACK_BARS:
            df = df.iloc[-LOOKBACK_BARS:]
        return df
    except Exception as exc:
        logger.warning("retrain_data_load_failed", extra={"error": str(exc)[:200]})
        return None


def _backtest_alpha(alpha_name: str, df) -> dict[str, Any] | None:
    try:
        from shared.alpha import get_alpha
        from shared.backtest import BacktestRunner, CostModel
        from shared.backtest.walk_forward import walk_forward
    except Exception as exc:
        logger.warning("retrain_imports_failed", extra={"error": str(exc)[:200]})
        return None

    try:
        alpha = get_alpha(alpha_name)
    except KeyError:
        return None

    cost = CostModel(commission_bps=4.0, slippage_bps=2.0, impact_coef=0.10)
    runner = BacktestRunner(cost_model=cost, periods_per_year=24 * 365, n_trials=5)
    full = runner.run(alpha, df)
    wf = walk_forward(alpha, df, n_windows=5, train_ratio=0.6, cost_model=cost, periods_per_year=24 * 365)

    return {
        "full_metrics": full.metrics,
        "oos_sharpe": float(wf.oos_aggregate.get("sharpe", 0.0)),
        "consistency": float(wf.consistency_score),
        "decay": float(wf.sharpe_decay),
        "evaluated_at": datetime.now(UTC).isoformat(),
        "n_bars": int(len(df)),
    }


async def _list_strategies(status: str | None = None) -> list[dict]:
    params = {}
    if status:
        params["status"] = status
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{REGISTRY_BASE}/strategies", params=params)
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.warning("list_strategies_failed", extra={"error": str(exc)[:200]})
    return []


async def _patch_status(strategy_id: str, new_status: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{REGISTRY_BASE}/strategies/{strategy_id}/status",
                json={"status": new_status},
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("patch_status_failed", extra={"strategy_id": strategy_id, "error": str(exc)[:200]})
        return False


async def _attach_backtest(strategy_id: str, payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{REGISTRY_BASE}/strategies/{strategy_id}/backtest",
                json=payload,
            )
            return resp.status_code == 200
    except Exception:
        return False


async def retrain_pass() -> dict[str, Any]:
    """Single pass of the retrain loop. Returns a summary dict."""
    df = _load_recent_ohlcv()
    if df is None:
        return {"status": "skipped", "reason": "no_real_data"}

    summary = {"evaluated": [], "demoted": [], "reactivated": [], "errors": []}

    # Evaluate ACTIVE strategies — demote on degradation
    active_list = await _list_strategies(status="ACTIVE")
    for s in active_list:
        alpha_name = (s.get("indicators") or [None])[0]
        if not alpha_name:
            continue
        result = _backtest_alpha(alpha_name, df)
        if result is None:
            summary["errors"].append({"id": s["id"], "reason": "backtest_failed"})
            continue
        summary["evaluated"].append({
            "id": s["id"], "name": s.get("name"), "alpha": alpha_name,
            "oos_sharpe": result["oos_sharpe"], "consistency": result["consistency"],
        })

        # Persist the new metrics
        await _attach_backtest(s["id"], {
            "status": "PASSED" if result["oos_sharpe"] >= REACTIVATION_THRESHOLD else "FAILED",
            "alpha_name": alpha_name,
            "engine": "shared.backtest.runner.retrain",
            "metrics": result["full_metrics"],
            "oos_sharpe": result["oos_sharpe"],
            "consistency": result["consistency"],
            "evaluated_at": result["evaluated_at"],
            "n_bars": result["n_bars"],
        })

        if result["oos_sharpe"] < DEGRADATION_THRESHOLD:
            ok = await _patch_status(s["id"], "PAUSED")
            if ok:
                summary["demoted"].append({
                    "id": s["id"], "name": s.get("name"),
                    "oos_sharpe": result["oos_sharpe"],
                })

    # DSR/PBO institutional gate (López de Prado) — runs alpha_gate on the
    # meta-ensemble config if available. Failure here is non-blocking: we
    # log a warning but don't demote (the canary cron handles escalation).
    try:
        gate_report = _run_alpha_gate()
        if gate_report is not None:
            summary["alpha_gate"] = gate_report
            if not gate_report.get("passed", True):
                logger.warning(
                    "alpha_gate_fail_in_retrain",
                    extra={
                        "dsr_verdict": gate_report.get("measured", {}).get("dsr_verdict"),
                        "pbo": gate_report.get("measured", {}).get("pbo"),
                    },
                )
    except Exception as exc:
        logger.debug("alpha_gate_skipped: %s", str(exc)[:100])

    # Evaluate PAUSED strategies — reactivate if recovered
    paused_list = await _list_strategies(status="PAUSED")
    for s in paused_list:
        alpha_name = (s.get("indicators") or [None])[0]
        if not alpha_name:
            continue
        result = _backtest_alpha(alpha_name, df)
        if result is None:
            continue
        if result["oos_sharpe"] >= REACTIVATION_THRESHOLD:
            ok = await _patch_status(s["id"], "ACTIVE")
            if ok:
                summary["reactivated"].append({
                    "id": s["id"], "name": s.get("name"),
                    "oos_sharpe": result["oos_sharpe"],
                })

    return summary


async def run_forever() -> None:
    while True:
        try:
            summary = await retrain_pass()
            logger.info("retrain_pass_complete", extra={"summary": summary})
        except Exception:
            logger.exception("retrain_pass_failed")
        await asyncio.sleep(RETRAIN_INTERVAL_SECONDS)
