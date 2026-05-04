#!/usr/bin/env python3
"""Fetch historical Fear & Greed Index from alternative.me API.

Free API, no auth needed. Returns daily values from 2018-02-01 onward.
Saves to data/external/fear_greed.csv.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import json
import urllib.request
import pandas as pd

OUT_DIR = REPO_ROOT / "data" / "external"
OUT_PATH = OUT_DIR / "fear_greed.csv"

# Max limit = 0 means all available data
API_URL = "https://api.alternative.me/fng/?limit=0&format=json"


def fetch() -> pd.DataFrame:
    print("Fetching Fear & Greed Index...")
    req = urllib.request.Request(API_URL, headers={"User-Agent": "quant-engine/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    records = data.get("data", [])
    print(f"  Got {len(records)} daily records")

    rows = []
    for r in records:
        ts = datetime.fromtimestamp(int(r["timestamp"]), tz=timezone.utc)
        rows.append({
            "timestamp": ts,
            "fng_value": int(r["value"]),
            "fng_class": r["value_classification"],
        })

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return df


def main():
    df = fetch()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"  Saved to {OUT_PATH}")
    print(f"  Range: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print(f"  Mean: {df['fng_value'].mean():.1f}, Std: {df['fng_value'].std():.1f}")
    print(f"  Min: {df['fng_value'].min()}, Max: {df['fng_value'].max()}")


if __name__ == "__main__":
    main()
