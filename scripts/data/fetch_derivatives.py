#!/usr/bin/env python3
"""Fetch historical derivatives data from Binance Futures API.

Four data types (all free, no auth):
  1. Open Interest History — total open contracts
  2. Global Long/Short Ratio — account-level sentiment
  3. Top Trader Position Ratio — whale positioning
  4. Taker Buy/Sell Volume — aggressive flow

Usage:
  python fetch_derivatives.py --symbols BTCUSDT ETHUSDT --period 1h --types oi lsr top taker
  python fetch_derivatives.py --all  # all 5 production symbols, all types
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

BASE_URL = "https://fapi.binance.com"
OUT_DIR = REPO_ROOT / "data" / "derivatives"

ENDPOINTS = {
    "oi":    "/futures/data/openInterestHist",
    "lsr":   "/futures/data/globalLongShortAccountRatio",
    "top":   "/futures/data/topLongShortPositionRatio",
    "taker": "/futures/data/takerlongshortRatio",
}

COLUMNS = {
    "oi":    ["timestamp", "symbol", "sumOpenInterest", "sumOpenInterestValue"],
    "lsr":   ["timestamp", "symbol", "longShortRatio", "longAccount", "shortAccount"],
    "top":   ["timestamp", "symbol", "longShortRatio", "longAccount", "shortAccount"],
    "taker": ["timestamp", "symbol", "buySellRatio", "buyVol", "sellVol"],
}

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

PERIOD_MS = {
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
}

MAX_RETRIES = 5
SLEEP_BETWEEN = 0.3
LIMIT = 500


def fetch_chunk(
    endpoint: str,
    symbol: str,
    period: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Fetch one chunk of data with retry logic."""
    url = (
        f"{BASE_URL}{endpoint}"
        f"?symbol={symbol}&period={period}&limit={LIMIT}"
        f"&startTime={start_ms}&endTime={end_ms}"
    )
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant-engine/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 418):
                wait = 30 * (attempt + 1)
                print(f"    Rate limited ({e.code}), waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    HTTP {e.code} on attempt {attempt + 1}")
                time.sleep(2 * (attempt + 1))
        except Exception as e:
            print(f"    Error on attempt {attempt + 1}: {e}")
            time.sleep(2 * (attempt + 1))
    return []


def fetch_full_history(
    data_type: str,
    symbol: str,
    period: str,
    years: float = 2.0,
) -> pd.DataFrame:
    """Fetch full history via paginated API calls."""
    endpoint = ENDPOINTS[data_type]
    columns = COLUMNS[data_type]
    period_ms = PERIOD_MS[period]

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - int(years * 365.25 * 24 * 3600 * 1000)

    all_records: list[dict] = []
    cursor = start_ms
    calls = 0

    while cursor < now_ms:
        chunk_end = cursor + LIMIT * period_ms
        data = fetch_chunk(endpoint, symbol, period, cursor, min(chunk_end, now_ms))
        calls += 1

        if not data:
            cursor = chunk_end
            time.sleep(SLEEP_BETWEEN)
            continue

        all_records.extend(data)

        # Advance cursor past last record
        last_ts = max(int(r.get("timestamp", 0)) for r in data)
        cursor = last_ts + period_ms

        time.sleep(SLEEP_BETWEEN)

    if not all_records:
        return pd.DataFrame(columns=columns)

    # Parse
    rows = []
    for r in all_records:
        ts = datetime.fromtimestamp(int(r["timestamp"]) / 1000, tz=timezone.utc)
        row = {"timestamp": ts, "symbol": symbol}
        for col in columns[2:]:
            row[col] = float(r.get(col, 0))
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    print(f"    {data_type}: {len(df)} records, {calls} API calls, "
          f"{df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}")
    return df


def main():
    parser = argparse.ArgumentParser(description="Fetch Binance derivatives data")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--period", default="1h", choices=["1h", "4h", "1d"])
    parser.add_argument("--types", nargs="+", default=["oi", "lsr", "top", "taker"])
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--all", action="store_true",
                        help="Fetch all types for all default symbols")
    args = parser.parse_args()

    # V10: cron entry passes "--types oi,lsr,top,taker" (comma-separated
    # single token), but nargs="+" only splits on whitespace, so the
    # whole CSV string ended up as one list element and the ENDPOINTS
    # lookup KeyError'd on every 6h fire (silent — derivatives.log
    # didn't even exist until V1 explained why). Accept both formats.
    def _flatten_csv(items):
        out = []
        for it in items:
            out.extend(p.strip() for p in str(it).split(",") if p.strip())
        return out

    args.symbols = _flatten_csv(args.symbols)
    args.types = _flatten_csv(args.types)

    if args.all:
        args.symbols = DEFAULT_SYMBOLS
        args.types = ["oi", "lsr", "top", "taker"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for symbol in args.symbols:
        print(f"\n{symbol}:")
        for dtype in args.types:
            df = fetch_full_history(dtype, symbol, args.period, args.years)
            if len(df) > 0:
                out_path = OUT_DIR / f"{symbol}_{dtype}_{args.period}.csv"
                df.to_csv(out_path, index=False)
                print(f"    Saved: {out_path.name}")
            else:
                print(f"    {dtype}: no data")

    print("\nDone.")


if __name__ == "__main__":
    main()
