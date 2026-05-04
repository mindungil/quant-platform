#!/usr/bin/env python3
"""Fetch perpetual funding rate history from Binance Futures public API.

Funding is settled every 8 hours (00:00, 08:00, 16:00 UTC).
A positive funding rate means longs pay shorts.

For a long position held 1 bar (1h), the cost per 8h settlement is:
    funding_cost = position_notional × funding_rate
    hourly_cost  = funding_rate / 8  (amortized)

This is ADDITIVE to the transaction cost model.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

UTC = timezone.utc
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
LIMIT = 1000


def fetch_chunk(symbol: str, start_ms: int, end_ms: int) -> list:
    qs = f"?symbol={symbol}&startTime={start_ms}&endTime={end_ms}&limit={LIMIT}"
    url = FUNDING_URL + qs
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant-funding/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            time.sleep(2 * (attempt + 1))
    return []


def fetch_full(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows = []
    cursor = start_ms
    step_ms = LIMIT * 8 * 3600 * 1000  # 1000 funding events × 8h each

    while cursor < end_ms:
        chunk = fetch_chunk(symbol, cursor, min(cursor + step_ms, end_ms))
        if not chunk:
            cursor += step_ms
            continue
        rows.extend(chunk)
        last_ts = chunk[-1].get("fundingTime", cursor)
        cursor = last_ts + 1
        time.sleep(0.3)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df.set_index("timestamp")[["symbol", "fundingRate"]]
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def main() -> int:
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
    start = datetime(2019, 9, 1, tzinfo=UTC)  # Binance Futures launch ~Sep 2019
    end = datetime.now(UTC)
    out_dir = REPO_ROOT / "data" / "funding"
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        print(f"\n→ {symbol}")
        df = fetch_full(symbol, start, end)
        if df.empty:
            print(f"  no data")
            continue
        out_path = out_dir / f"{symbol}_funding.csv"
        df.to_csv(out_path)
        print(f"  {len(df)} funding events  {df.index[0].date()}..{df.index[-1].date()}")

        # Stats
        annual_rate = df["fundingRate"].mean() * 3 * 365 * 100  # 3x/day × 365d → annual %
        print(f"  mean funding rate: {df['fundingRate'].mean()*100:.4f}% per 8h")
        print(f"  annualized (if always long): {annual_rate:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
