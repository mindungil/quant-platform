#!/usr/bin/env python3
"""Fetch real OHLCV klines from Binance public API.

No API key needed (Binance public klines endpoint is unauthenticated).
Stores results as parquet under data/ohlcv/{symbol}_{interval}.parquet so
the alpha library can backtest against real history without depending on
the market-data service.

Usage:
    python3 scripts/data/fetch_binance_klines.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT \\
        --interval 1h \\
        --years 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

UTC = timezone.utc
BINANCE_REST = "https://api.binance.com/api/v3/klines"
BINANCE_LIMIT = 1000  # max bars per request

# Approximate ms per bar — used to step the cursor
INTERVAL_MS = {
    "1m":   60 * 1000,
    "5m":   5 * 60 * 1000,
    "15m":  15 * 60 * 1000,
    "30m":  30 * 60 * 1000,
    "1h":   60 * 60 * 1000,
    "4h":   4 * 60 * 60 * 1000,
    "1d":   24 * 60 * 60 * 1000,
}


def fetch_chunk(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch up to BINANCE_LIMIT bars between [start_ms, end_ms).

    Returns the raw kline list. Retries on transient HTTP errors.
    """
    qs = (
        f"?symbol={symbol}"
        f"&interval={interval}"
        f"&startTime={start_ms}"
        f"&endTime={end_ms}"
        f"&limit={BINANCE_LIMIT}"
    )
    url = BINANCE_REST + qs
    last_err = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant-data-fetcher/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code == 418:
                # Rate limited / banned — back off hard
                wait = 30 * (attempt + 1)
                print(f"  rate-limited (HTTP {e.code}), sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                time.sleep(2 * (attempt + 1))
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {symbol} {interval} after retries: {last_err}")


def fetch_full_history(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    sleep_per_call: float = 0.20,
) -> pd.DataFrame:
    """Walk forward in BINANCE_LIMIT-sized chunks until end_ms."""
    step_ms = INTERVAL_MS[interval] * BINANCE_LIMIT
    rows: list = []
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + step_ms, end_ms)
        klines = fetch_chunk(symbol, interval, cursor, chunk_end)
        if not klines:
            cursor = chunk_end
            continue
        rows.extend(klines)
        # Advance to one bar past the last received
        last_open = klines[-1][0]
        next_cursor = last_open + INTERVAL_MS[interval]
        if next_cursor <= cursor:
            # Safety: don't loop forever if Binance returns weird data
            cursor = chunk_end
        else:
            cursor = next_cursor
        time.sleep(sleep_per_call)

    if not rows:
        return pd.DataFrame()

    # Binance kline schema: open_time, open, high, low, close, volume, close_time, qav, n_trades, taker_buy_base, taker_buy_quote, ignore
    df = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "n_trades",
            "taker_buy_base", "taker_buy_quote", "_ignore",
        ],
    )
    for col in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df.drop(columns=["_ignore"])
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--interval", default="1h", choices=sorted(INTERVAL_MS.keys()))
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--start", default=None, help="ISO date YYYY-MM-DD (overrides --years)")
    ap.add_argument("--end", default=None, help="ISO date YYYY-MM-DD")
    ap.add_argument("--suffix", default=None, help="extra filename suffix, e.g. '_2018_2021'")
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "ohlcv"))
    ap.add_argument("--sleep", type=float, default=0.20)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.start:
        start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    else:
        start = datetime.now(UTC) - timedelta(days=int(365 * args.years))
    if args.end:
        end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    else:
        end = datetime.now(UTC)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"Fetching {len(symbols)} symbols, interval={args.interval}, "
          f"window={start.date()}..{end.date()}")

    summary = []
    for symbol in symbols:
        suffix = args.suffix or ""
        out_path = out_dir / f"{symbol}_{args.interval}{suffix}.parquet"
        print(f"\n→ {symbol}")
        df = fetch_full_history(symbol, args.interval, start_ms, end_ms, args.sleep)
        if df.empty:
            print(f"  no data returned for {symbol}")
            summary.append({"symbol": symbol, "rows": 0, "path": None})
            continue
        try:
            df.to_parquet(out_path)
        except Exception:
            # Fall back to CSV if parquet engine missing
            out_path = out_dir / f"{symbol}_{args.interval}{suffix}.csv"
            df.to_csv(out_path)
        first = df.index[0]
        last = df.index[-1]
        print(f"  saved {len(df):>6} bars  {first.date()}..{last.date()}  →  {out_path.name}")
        summary.append({"symbol": symbol, "rows": int(len(df)), "path": str(out_path)})

    # Write a manifest
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(
            {
                "fetched_at": end.isoformat(),
                "interval": args.interval,
                "years": args.years,
                "symbols": summary,
            },
            f,
            indent=2,
        )
    print(f"\nManifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
