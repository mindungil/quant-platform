#!/usr/bin/env python3
"""Fetch traditional finance data for cross-asset signals.

Uses free Yahoo Finance-compatible endpoints (no auth required):
  - S&P 500 (^GSPC) via Binance or proxy
  - DXY (Dollar index)
  - Gold (XAUUSD)

Since direct Yahoo requires headers, we use a simple approach:
fetch crypto-adjacent proxies from Binance or free APIs.
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

OUT_DIR = REPO_ROOT / "data" / "tradfi"


def fetch_dxy_proxy() -> pd.DataFrame:
    """DXY proxy via stablecoin deviations.

    When USD strengthens (DXY up), crypto tends to fall.
    We proxy this via USDT/BUSD implicit rate from Binance.
    For real DXY, would need Yahoo/FRED API with auth.

    Alternative: use BTC inverse correlation as DXY proxy.
    """
    # Fetch EURUSDT (inverse proxy for DXY)
    print("Fetching EURUSDT as DXY proxy...")
    url = "https://api.binance.com/api/v3/klines?symbol=EURUSDT&interval=1d&limit=1000"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quant/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        rows = []
        for k in data:
            ts = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
            rows.append({
                "timestamp": ts,
                "eurusd_close": float(k[4]),
                "eurusd_volume": float(k[5]),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  Error: {e}")
        return pd.DataFrame()


def fetch_gold_proxy() -> pd.DataFrame:
    """Gold proxy via PAXGUSDT (Pax Gold) on Binance."""
    print("Fetching PAXGUSDT as Gold proxy...")
    url = "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=1d&limit=1000"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quant/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        rows = []
        for k in data:
            ts = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
            rows.append({
                "timestamp": ts,
                "gold_close": float(k[4]),
                "gold_volume": float(k[5]),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  Error: {e}")
        return pd.DataFrame()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # DXY proxy
    dxy = fetch_dxy_proxy()
    if len(dxy) > 0:
        print(f"  EURUSDT: {len(dxy)} days ({dxy['timestamp'].iloc[0].date()} → {dxy['timestamp'].iloc[-1].date()})")

    # Gold proxy
    gold = fetch_gold_proxy()
    if len(gold) > 0:
        print(f"  PAXGUSDT: {len(gold)} days ({gold['timestamp'].iloc[0].date()} → {gold['timestamp'].iloc[-1].date()})")

    # Merge
    if len(dxy) > 0 and len(gold) > 0:
        dxy = dxy.set_index("timestamp")
        gold = gold.set_index("timestamp")
        merged = dxy.join(gold, how="outer").sort_index().ffill()
        out_path = OUT_DIR / "tradfi_daily.csv"
        merged.to_csv(out_path)
        print(f"\nSaved {len(merged)} days to {out_path}")
    elif len(dxy) > 0:
        out_path = OUT_DIR / "tradfi_daily.csv"
        dxy.set_index("timestamp").to_csv(out_path)
        print(f"\nSaved DXY only: {len(dxy)} days to {out_path}")


if __name__ == "__main__":
    main()
