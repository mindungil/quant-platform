#!/usr/bin/env python3
"""Monthly automated alpha mining.

Cron entry point: runs 1st of each month at 04:00 UTC.
  1. Loads current config and existing alpha positions
  2. Runs AlphaMiner on all available data per symbol
  3. Validates candidates with walk-forward + 5-gate filter
  4. If any promoted: logs result, saves model info
  5. Logs everything to data/metrics/mining_log/
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from shared.alpha.base import AlphaConfig  # noqa: E402
from shared.alpha.registry import get_alpha  # noqa: E402
from shared.engine.alpha_miner import AlphaMiner, AlphaMinerConfig  # noqa: E402
from shared.engine.config import load_config  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "ohlcv"
FUNDING_DIR = REPO_ROOT / "data" / "funding"
CONFIG_PATH = REPO_ROOT / "config" / "v4_production.json"
UTC = timezone.utc


def load_symbol_data(symbol: str) -> pd.DataFrame:
    """Load full stitched OHLCV data for a symbol."""
    for suffix in ("_stitched", ""):
        p = DATA_DIR / f"{symbol}_1h{suffix}.csv"
        if p.exists():
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            for c in ("open", "high", "low", "close", "volume"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            return df.dropna(subset=["close"]).sort_index()
    raise FileNotFoundError(f"no data for {symbol}")


def load_funding(symbol: str) -> pd.Series | None:
    """Load funding rate data if available."""
    p = FUNDING_DIR / f"{symbol}_funding.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").set_index("timestamp")
    if "fundingRate" in df.columns:
        return df["fundingRate"].astype(float)
    return None


def generate_existing_positions(
    dfs: dict[str, pd.DataFrame],
    cfg,
) -> dict[str, dict[str, pd.Series]]:
    """Generate current alpha positions for correlation screening."""
    positions: dict[str, dict[str, pd.Series]] = {}
    for sym, df in dfs.items():
        positions[sym] = {}
        for alpha_name, params in cfg.alphas.items():
            try:
                alpha_cfg = AlphaConfig(name=alpha_name, params=params)
                alpha = get_alpha(alpha_name, alpha_cfg)
                sig = alpha.generate(df)
                positions[sym][alpha_name] = sig.position
            except Exception as e:
                print(f"  Warning: failed to generate {alpha_name} for {sym}: {e}")
    return positions


def main() -> int:
    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M}] Alpha mining starting...")
    cfg = load_config(CONFIG_PATH)

    # Load data
    dfs: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for sym in cfg.symbols:
        try:
            dfs[sym] = load_symbol_data(sym)
            fr = load_funding(sym)
            if fr is not None:
                funding[sym] = fr
            print(f"  {sym}: {len(dfs[sym])} bars")
        except FileNotFoundError as e:
            print(f"  Skip {sym}: {e}")

    if len(dfs) < 3:
        print("Not enough symbols with data. Aborting.")
        return 1

    # Generate existing alpha positions for correlation screening
    print("Generating existing alpha positions...")
    existing = generate_existing_positions(dfs, cfg)

    # Run miner
    miner_config = AlphaMinerConfig(
        n_candidates=50,
        features_per_model=30,
        min_oos_sharpe=0.3,
        max_corr_existing=0.4,
        cost_bps=5.0,
    )
    miner = AlphaMiner(miner_config)
    result = miner.mine(dfs, funding, existing)

    # Report
    print(f"\n{'='*50}")
    print(f"  MINING COMPLETE")
    print(f"{'='*50}")
    print(f"  Candidates tested: {result.n_candidates_tested}")
    print(f"  Passed gates:      {result.n_passed_gates}")
    print(f"  Cumulative trials: {result.cumulative_trials}")
    print(f"  Promoted:          {len(result.promoted)}")

    for c in result.promoted:
        print(f"\n  Promoted: {c.candidate_id}")
        print(f"    Avg Sharpe:  {c.avg_oos_sharpe:.3f}")
        print(f"    Max Corr:    {c.max_corr_with_existing:.3f}")
        print(f"    Max DD:      {c.max_drawdown:.1%}")
        print(f"    Score:       {c.score:.3f}")
        print(f"    Per-symbol:  {c.oos_sharpes}")
        print(f"    Features:    {c.feature_names[:10]}...")

    if not result.promoted:
        print("\n  No candidates passed all gates this cycle.")

    print(f"\n[{datetime.now(UTC):%Y-%m-%d %H:%M}] Mining finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
