#!/usr/bin/env python3
"""Collect and persist alternative data sources for alpha research.

Fetches and stores historical + incremental data that the feature engine
already supports but lacks stored history for:

  1. Binance Futures derivatives (OI, LSR, Taker Buy/Sell) — all symbols
  2. Fear & Greed Index — incremental update
  3. Macro indicators (DXY, VIX proxies via Yahoo Finance)

Usage:
    python scripts/collect_alt_data.py --all           # full collection
    python scripts/collect_alt_data.py --derivatives    # only derivatives
    python scripts/collect_alt_data.py --macro          # only macro
    python scripts/collect_alt_data.py --fng            # only FNG update
    python scripts/collect_alt_data.py --incremental    # append new data only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import numpy as np
import pandas as pd

DATA_DIR = Path(REPO_ROOT) / "data"
DERIVATIVES_DIR = DATA_DIR / "derivatives"
EXTERNAL_DIR = DATA_DIR / "external"
MACRO_DIR = DATA_DIR / "macro"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def _ensure_dirs():
    for d in [DERIVATIVES_DIR, EXTERNAL_DIR, MACRO_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Binance Futures Derivatives Data
# ---------------------------------------------------------------------------

def _binance_futures_get(endpoint: str, params: dict, max_retries: int = 3) -> list:
    """GET from Binance Futures API with retry."""
    import urllib.request
    import urllib.parse

    base = "https://fapi.binance.com"
    qs = urllib.parse.urlencode(params)
    url = f"{base}{endpoint}?{qs}"

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant-collector/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  WARN: {endpoint} failed after {max_retries} retries: {e}")
                return []
            time.sleep(2 ** attempt)
    return []


def collect_open_interest(symbol: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Fetch open interest history from Binance Futures."""
    data = _binance_futures_get("/futures/data/openInterestHist", {
        "symbol": symbol, "period": period, "limit": limit,
    })
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    for col in ["sumOpenInterest", "sumOpenInterestValue"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def collect_long_short_ratio(symbol: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Fetch global long/short account ratio."""
    data = _binance_futures_get("/futures/data/globalLongShortAccountRatio", {
        "symbol": symbol, "period": period, "limit": limit,
    })
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    for col in ["longShortRatio", "longAccount", "shortAccount"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def collect_top_trader_lsr(symbol: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Fetch top trader long/short ratio (position-based)."""
    data = _binance_futures_get("/futures/data/topLongShortPositionRatio", {
        "symbol": symbol, "period": period, "limit": limit,
    })
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    for col in ["longShortRatio", "longAccount", "shortAccount"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def collect_taker_buy_sell(symbol: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Fetch taker buy/sell volume ratio."""
    data = _binance_futures_get("/futures/data/takerlongshortRatio", {
        "symbol": symbol, "period": period, "limit": limit,
    })
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    for col in ["buySellRatio", "buyVol", "sellVol"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def _save_with_dedup(df: pd.DataFrame, path: Path):
    """Append-safe save: merge with existing, deduplicate by index."""
    # Normalize to tz-naive UTC
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if path.exists():
        existing = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
        if existing.index.tz is not None:
            existing.index = existing.index.tz_localize(None)
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    df.to_csv(path, index=True, index_label="timestamp")
    return len(df)


def collect_derivatives(symbols: list[str] | None = None, incremental: bool = False):
    """Collect all derivatives data for given symbols."""
    symbols = symbols or SYMBOLS
    _ensure_dirs()

    collectors = {
        "oi_1h": collect_open_interest,
        "lsr_1h": collect_long_short_ratio,
        "top_1h": collect_top_trader_lsr,
        "taker_1h": collect_taker_buy_sell,
    }

    limit = 100 if incremental else 500

    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        for suffix, collector_fn in collectors.items():
            path = DERIVATIVES_DIR / f"{symbol}_{suffix}.csv"
            df = collector_fn(symbol, limit=limit)
            if df.empty:
                print(f"  {suffix}: no data returned")
                continue
            n = _save_with_dedup(df, path)
            print(f"  {suffix}: {len(df)} new rows → {n} total in {path.name}")
            time.sleep(0.5)  # rate limit courtesy


# ---------------------------------------------------------------------------
# 2. Fear & Greed Index (incremental)
# ---------------------------------------------------------------------------

def collect_fng(limit: int = 30):
    """Update Fear & Greed CSV with latest data."""
    import urllib.request
    _ensure_dirs()

    path = EXTERNAL_DIR / "fear_greed.csv"
    url = f"https://api.alternative.me/fng/?limit={limit}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quant-collector/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
    except Exception as e:
        print(f"FNG fetch failed: {e}")
        return

    records = []
    for item in raw.get("data", []):
        ts = datetime.utcfromtimestamp(int(item["timestamp"]))
        records.append({
            "timestamp": ts,
            "fng_value": int(item["value"]),
            "fng_class": item["value_classification"],
        })

    df = pd.DataFrame(records).set_index("timestamp").sort_index()
    n = _save_with_dedup(df, path)
    print(f"FNG: {len(df)} new rows → {n} total")


# ---------------------------------------------------------------------------
# 3. Macro Indicators (DXY, VIX proxies)
# ---------------------------------------------------------------------------

def collect_macro():
    """Fetch macro indicators via Yahoo Finance (no API key needed)."""
    import urllib.request
    _ensure_dirs()

    tickers = {
        "DX-Y.NYB": "dxy",       # US Dollar Index
        "^VIX": "vix",            # CBOE Volatility Index
        "^TNX": "us10y",          # 10-Year Treasury Yield
        "GC=F": "gold",           # Gold futures
    }

    for yf_ticker, name in tickers.items():
        try:
            # Yahoo Finance chart API (public, no key)
            import urllib.parse
            encoded = urllib.parse.quote(yf_ticker)
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
                f"?range=5y&interval=1d"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; quant-collector/1.0)",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read().decode())

            chart = raw["chart"]["result"][0]
            timestamps = chart["timestamp"]
            quotes = chart["indicators"]["quote"][0]

            df = pd.DataFrame({
                "timestamp": pd.to_datetime(timestamps, unit="s"),
                "close": quotes["close"],
                "high": quotes.get("high"),
                "low": quotes.get("low"),
                "volume": quotes.get("volume"),
            })
            df = df.dropna(subset=["close"]).set_index("timestamp").sort_index()

            path = MACRO_DIR / f"{name}_daily.csv"
            n = _save_with_dedup(df, path)
            print(f"Macro {name}: {len(df)} rows → {n} total in {path.name}")
            time.sleep(1)
        except Exception as e:
            print(f"Macro {name} failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Collect alternative data sources")
    ap.add_argument("--all", action="store_true", help="Collect everything")
    ap.add_argument("--derivatives", action="store_true")
    ap.add_argument("--fng", action="store_true")
    ap.add_argument("--macro", action="store_true")
    ap.add_argument("--incremental", action="store_true", help="Only fetch recent data")
    ap.add_argument("--symbols", default="", help="Comma-separated symbols for derivatives")
    args = ap.parse_args()

    if not any([args.all, args.derivatives, args.fng, args.macro]):
        args.all = True

    symbols = args.symbols.split(",") if args.symbols else SYMBOLS

    if args.all or args.derivatives:
        print("=== Collecting Derivatives Data ===")
        collect_derivatives(symbols, incremental=args.incremental)

    if args.all or args.fng:
        print("\n=== Collecting Fear & Greed Index ===")
        collect_fng(limit=10 if args.incremental else 365)

    if args.all or args.macro:
        print("\n=== Collecting Macro Data ===")
        collect_macro()

    print("\nDone.")


if __name__ == "__main__":
    main()
