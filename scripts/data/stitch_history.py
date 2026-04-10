#!/usr/bin/env python3
"""Stitch historical, gap, and recent OHLCV slices into a single continuous file.

Input files (1h bars):
  {sym}_1h_historical.csv   2018-01 → 2021-01
  {sym}_1h_gap.csv          2021-01 → 2023-04
  {sym}_1h.csv              2023-04 → 2026-04

Output:
  {sym}_1h_stitched.csv     2018-01 → 2026-04 (continuous, dedup'd, sorted)

Handles overlapping boundaries (keeps the first occurrence) and drops
any duplicate or non-monotonic rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "ohlcv"
SUFFIXES = ["_historical", "_gap", ""]


def load_one(symbol: str, suffix: str) -> pd.DataFrame | None:
    for ext in (".parquet", ".csv"):
        p = DATA_DIR / f"{symbol}_1h{suffix}{ext}"
        if p.exists():
            if ext == ".parquet":
                return pd.read_parquet(p)
            return pd.read_csv(p, index_col=0, parse_dates=True)
    return None


def stitch(symbol: str) -> pd.DataFrame | None:
    pieces = []
    for suf in SUFFIXES:
        df = load_one(symbol, suf)
        if df is not None:
            pieces.append(df)
            print(f"  {symbol}{suf or '_recent'}: {len(df)} rows  {df.index[0].date()} → {df.index[-1].date()}")
    if not pieces:
        print(f"  {symbol}: no data")
        return None

    out = pd.concat(pieces, axis=0)
    # Normalize tz — some files may be tz-naive, others tz-aware
    try:
        out.index = pd.to_datetime(out.index, utc=True)
    except Exception:
        pass
    out = out[~out.index.duplicated(keep="first")]
    out = out.sort_index()
    # Drop rows without a close price
    if "close" in out.columns:
        out = out.dropna(subset=["close"])
    return out


def main() -> int:
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "LINKUSDT"]
    for sym in symbols:
        print(f"\n→ {sym}")
        stitched = stitch(sym)
        if stitched is None or stitched.empty:
            print(f"  skipped {sym}")
            continue
        out_path = DATA_DIR / f"{sym}_1h_stitched.csv"
        stitched.to_csv(out_path)
        span_days = (stitched.index[-1] - stitched.index[0]).days
        print(
            f"  stitched → {out_path.name}: {len(stitched)} rows, "
            f"{stitched.index[0].date()} → {stitched.index[-1].date()} "
            f"({span_days / 365.25:.2f} years)"
        )

        # Gap audit — look for missing hours
        diffs = stitched.index.to_series().diff().dropna()
        big_gaps = diffs[diffs > pd.Timedelta(hours=2)]
        if len(big_gaps):
            print(f"  ⚠ {len(big_gaps)} gaps > 2h (max={big_gaps.max()})")
            print(big_gaps.head(5).to_string())
        else:
            print("  ✓ no gaps > 2h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
