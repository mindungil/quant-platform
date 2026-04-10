#!/usr/bin/env python3
"""Weekly automated parameter refit.

Cron entry point: runs every Sunday at 02:00 UTC.
  1. Loads current config
  2. Fetches last 180 days of data per symbol
  3. Runs RollingRefitter on each alpha × symbol
  4. If any promoted: archives old config, writes new one
  5. Logs everything to data/metrics/refit_log/
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from shared.engine.config import EngineConfig, load_config, save_config  # noqa: E402
from shared.engine.refit import RollingRefitter  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "ohlcv"
CONFIG_PATH = REPO_ROOT / "config" / "v4_production.json"
ARCHIVE_DIR = REPO_ROOT / "config" / "archive"
LOG_DIR = REPO_ROOT / "data" / "metrics" / "refit_log"
UTC = timezone.utc


def load_recent(symbol: str, days: int = 200) -> pd.DataFrame:
    for suffix in ("_stitched", ""):
        p = DATA_DIR / f"{symbol}_1h{suffix}.csv"
        if p.exists():
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            for c in ("open", "high", "low", "close", "volume"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["close"]).sort_index()
            # Take last N days
            n = days * 24
            return df.iloc[-n:] if len(df) > n else df
    raise FileNotFoundError(f"no data for {symbol}")


def main() -> int:
    cfg = load_config(CONFIG_PATH)
    print(f"[{datetime.now(UTC).isoformat()}] Weekly refit starting")
    print(f"  Symbols: {cfg.symbols}")
    print(f"  Current params: {json.dumps(cfg.alphas, indent=2)}")

    # Load data
    dfs = {}
    for sym in cfg.symbols[:2]:  # BTC + ETH for refit (fastest, most liquid)
        try:
            dfs[sym] = load_recent(sym, days=cfg.refit_lookback_days + cfg.refit_oos_days + 30)
            print(f"  {sym}: {len(dfs[sym])} bars")
        except FileNotFoundError as e:
            print(f"  {sym}: skip ({e})")

    if not dfs:
        print("  No data loaded. Aborting.")
        return 1

    # Run refit
    refitter = RollingRefitter(
        current_params=cfg.alphas,
        lookback_days=cfg.refit_lookback_days,
        oos_days=cfg.refit_oos_days,
        significance=cfg.refit_significance,
        safety_margin=cfg.refit_safety_margin,
    )
    results = refitter.refit_all(dfs, require_majority=cfg.refit_require_majority)

    # Log results
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"refit_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}.json"
    log_data = []
    any_promoted = False
    for r in results:
        print(f"  {r.reason}")
        log_data.append({
            "alpha": r.alpha_name,
            "current_params": r.current_params,
            "candidate_params": r.candidate_params,
            "current_sharpe": r.current_oos_sharpe,
            "candidate_sharpe": r.candidate_oos_sharpe,
            "p_value": r.p_value,
            "promoted": r.promoted,
            "reason": r.reason,
        })
        if r.promoted:
            any_promoted = True

    with open(log_path, "w") as f:
        json.dump({"timestamp": datetime.now(UTC).isoformat(), "results": log_data}, f, indent=2)
    print(f"  Log: {log_path}")

    # Apply promotions
    if any_promoted:
        # Archive current config
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        archive_name = f"v4_production_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}.json"
        shutil.copy2(CONFIG_PATH, ARCHIVE_DIR / archive_name)
        print(f"  Archived old config: {archive_name}")

        # Update config with promoted params
        for r in results:
            if r.promoted:
                cfg.alphas[r.alpha_name] = r.candidate_params
                print(f"  UPDATED {r.alpha_name}: {r.candidate_params}")

        save_config(cfg, CONFIG_PATH)
        print(f"  New config saved to {CONFIG_PATH}")
    else:
        print("  No promotions. Config unchanged.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
