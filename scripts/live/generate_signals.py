#!/usr/bin/env python3
"""v4 Live Signal Generator.

Fetches the latest OHLCV from Binance, runs the v4 pruned engine,
and outputs:
  1) Current recommended position per symbol (-1 to +1)
  2) Position change from previous bar (trade signal)
  3) Recent 30-day rolling Sharpe (engine health check)
  4) Per-alpha attribution (which alpha is driving the signal)

Designed to be run hourly via cron or manually. No API keys needed
(public OHLCV endpoint). Does NOT execute trades — output only.

Usage:
    python3 scripts/live/generate_signals.py
    python3 scripts/live/generate_signals.py --symbols BTCUSDT,ETHUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from shared.alpha.base import AlphaConfig  # noqa: E402
from shared.alpha.kalman_trend import KalmanTrendAlpha  # noqa: E402
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha  # noqa: E402
from shared.alpha.trend_breakout import TrendBreakoutAlpha  # noqa: E402
from shared.backtest.metrics import sharpe_ratio  # noqa: E402
from shared.portfolio import EnsembleAllocator, EnsembleConfig  # noqa: E402
from shared.regime import VolTrendRegime  # noqa: E402

# Use the existing fetcher
from scripts.data.fetch_binance_klines import fetch_full_history, INTERVAL_MS  # noqa: E402

# Import v4.1 config from single source of truth
sys.path.insert(0, str(REPO_ROOT / "scripts" / "bootstrap"))
from _common import BEST_PARAMS, AFFINITY, SIZING_MODE, TURNOVER_DEADZONE, PPY  # noqa: E402

UTC = timezone.utc


def fetch_recent(symbol: str, lookback_days: int = 120) -> pd.DataFrame:
    """Fetch recent OHLCV from Binance public API."""
    end = datetime.now(UTC)
    start = end - timedelta(days=lookback_days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    df = fetch_full_history(symbol, "1h", start_ms, end_ms, sleep_per_call=0.15)
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).sort_index()


def make_alpha(name: str, params: dict):
    cfg = AlphaConfig(name=name, params=params)
    if name == "kalman_trend":
        return KalmanTrendAlpha(cfg)
    elif name == "momentum_ensemble":
        return MomentumEnsembleAlpha(cfg)
    elif name == "trend_breakout":
        return TrendBreakoutAlpha(cfg)
    raise ValueError(name)


def run_engine(symbol: str, df: pd.DataFrame) -> dict:
    """Run v4 engine on a single symbol, return current signal + diagnostics."""
    ret = df["close"].pct_change().fillna(0.0)

    # Build alpha positions
    alpha_pos = {}
    alpha_current = {}
    for name, params in BEST_PARAMS.items():
        try:
            pos = make_alpha(name, params).generate(df).position
            alpha_pos[name] = pos
            alpha_current[name] = float(pos.iloc[-1])
        except Exception as exc:
            alpha_current[name] = f"error: {exc}"

    if not alpha_pos:
        return {"error": "no alphas generated"}

    # Regime
    regime = VolTrendRegime().fit_predict(df)
    current_regime = regime.proba.iloc[-1].idxmax() if regime.proba is not None else "?"

    # Ensemble
    cfg = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=PPY,
        turnover_deadzone=TURNOVER_DEADZONE,
        sizing_mode=SIZING_MODE,
    )
    res = EnsembleAllocator(cfg).combine(
        alpha_pos, ret, regime_proba=regime.proba, regime_alpha_affinity=AFFINITY
    )
    pos = res.target_position.fillna(0.0)
    current_pos = float(pos.iloc[-1])
    prev_pos = float(pos.iloc[-2]) if len(pos) > 1 else 0.0
    delta_pos = current_pos - prev_pos

    # Recent performance (30-day rolling)
    pnl = (pos * ret).values
    recent_30d = pnl[-720:] if len(pnl) >= 720 else pnl
    recent_sharpe = sharpe_ratio(recent_30d, periods_per_year=PPY)

    # Price info
    last_close = float(df["close"].iloc[-1])
    last_time = str(df.index[-1])

    return {
        "symbol": symbol,
        "timestamp": last_time,
        "price": last_close,
        "regime": current_regime,
        "target_position": round(current_pos, 4),
        "position_change": round(delta_pos, 4),
        "action": "BUY" if delta_pos > 0.02 else ("SELL" if delta_pos < -0.02 else "HOLD"),
        "rolling_30d_sharpe": round(recent_sharpe, 2),
        "alpha_positions": {k: round(v, 4) if isinstance(v, float) else v for k, v in alpha_current.items()},
        "alpha_weights": {k: round(v, 4) for k, v in res.alpha_weights.iloc[-1].to_dict().items()} if len(res.alpha_weights) > 0 else {},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,LINKUSDT")
    ap.add_argument("--lookback", type=int, default=120, help="days of history to fetch")
    ap.add_argument("--json", action="store_true", help="output as JSON")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    signals = []

    for symbol in symbols:
        print(f"Fetching {symbol}...", end=" ", flush=True)
        try:
            df = fetch_recent(symbol, args.lookback)
            print(f"{len(df)} bars", flush=True)
            sig = run_engine(symbol, df)
            signals.append(sig)
        except Exception as exc:
            print(f"ERROR: {exc}")
            signals.append({"symbol": symbol, "error": str(exc)})

    if args.json:
        print(json.dumps(signals, indent=2, default=str))
        return 0

    # Pretty print
    print("\n" + "=" * 70)
    print(f"  v4 LIVE SIGNALS — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    for sig in signals:
        if "error" in sig:
            print(f"\n  {sig['symbol']}: ERROR — {sig['error']}")
            continue

        pos = sig["target_position"]
        action = sig["action"]
        sh30 = sig["rolling_30d_sharpe"]

        # Color-code the action
        action_str = f"{'>>':>2} {action}" if action != "HOLD" else "   HOLD"

        print(f"\n  {sig['symbol']}  ${sig['price']:,.2f}  regime={sig['regime']}")
        print(f"    Position: {pos:+.4f}  Δ={sig['position_change']:+.4f}  {action_str}")
        print(f"    30d Sharpe: {sh30:+.2f}  {'OK' if sh30 > 0.5 else 'WEAK' if sh30 > 0 else 'BAD'}")
        print(f"    Alphas: ", end="")
        for name, val in sig["alpha_positions"].items():
            if isinstance(val, float):
                print(f"{name}={val:+.3f} ", end="")
        print()

    # Summary
    print("\n" + "-" * 70)
    positions = {s["symbol"]: s["target_position"] for s in signals if "error" not in s}
    net_exposure = sum(positions.values())
    print(f"  Net exposure: {net_exposure:+.2f}")
    print(f"  Positions: {', '.join(f'{k}={v:+.3f}' for k, v in positions.items())}")

    # Health check
    sharpes = [s["rolling_30d_sharpe"] for s in signals if "error" not in s]
    avg_sh = np.mean(sharpes) if sharpes else 0
    health = "HEALTHY" if avg_sh > 0.5 else "CAUTION" if avg_sh > 0 else "DANGER"
    print(f"  Engine health: {health} (avg 30d Sharpe = {avg_sh:+.2f})")
    print("=" * 70)

    # Save to file
    out_path = REPO_ROOT / "data" / "signals" / f"signals_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(signals, f, indent=2, default=str)
    print(f"\nSaved to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
