#!/usr/bin/env python3
"""Fetch OHLCV data for expanded symbol set.

Downloads hourly klines for 10 additional symbols from Binance.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

OUT_DIR = REPO_ROOT / "data" / "ohlcv"

EXPANDED_SYMBOLS = [
    "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "ADAUSDT",
    "LTCUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT",
]

BASE_URL = "https://api.binance.com"
LIMIT = 1000
INTERVAL = "1h"
INTERVAL_MS = 3_600_000


def fetch_klines_chunk(symbol: str, start_ms: int, end_ms: int) -> list:
    url = (
        f"{BASE_URL}/api/v3/klines"
        f"?symbol={symbol}&interval={INTERVAL}&limit={LIMIT}"
        f"&startTime={start_ms}&endTime={end_ms}"
    )
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 418):
                time.sleep(30 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return []


def fetch_symbol(symbol: str, years: float = 2.0) -> pd.DataFrame:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - int(years * 365.25 * 24 * 3600 * 1000)

    all_rows = []
    cursor = start_ms
    calls = 0

    while cursor < now_ms:
        chunk = fetch_klines_chunk(symbol, cursor, now_ms)
        calls += 1
        if not chunk:
            break

        for k in chunk:
            ts = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
            all_rows.append({
                "timestamp": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]),
                "n_trades": int(k[8]),
                "taker_buy_base": float(k[9]),
                "taker_buy_quote": float(k[10]),
            })

        cursor = int(chunk[-1][0]) + INTERVAL_MS
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    df = df.set_index("timestamp")
    return df


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    years = 2.0

    for symbol in EXPANDED_SYMBOLS:
        print(f"{symbol}...", end=" ", flush=True)
        df = fetch_symbol(symbol, years)
        if len(df) > 0:
            out_path = OUT_DIR / f"{symbol}_1h.csv"
            df.to_csv(out_path)
            print(f"{len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
        else:
            print("NO DATA")

    print("\nDone.")


if __name__ == "__main__":
    main()
