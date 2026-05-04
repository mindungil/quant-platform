#!/usr/bin/env python3
"""Fetch on-chain BTC metrics from free APIs.

Sources (no auth required):
  - Blockchain.com: tx volume, active addresses, hash rate, mempool size
  - Mempool.space: hashrate, difficulty

These are daily metrics that provide fundamentally different information
from price/volume — network health, adoption, and miner behavior.
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

OUT_DIR = REPO_ROOT / "data" / "onchain"

BLOCKCHAIN_CHARTS = {
    "tx_volume_usd": "estimated-transaction-volume-usd",
    "n_transactions": "n-transactions",
    "active_addresses": "n-unique-addresses",
    "hash_rate": "hash-rate",
    "mempool_size": "mempool-size",
    "avg_block_size": "avg-block-size",
    "miners_revenue": "miners-revenue",
    "difficulty": "difficulty",
}


def fetch_blockchain_chart(chart_name: str, timespan: str = "2years") -> pd.DataFrame:
    """Fetch a chart from blockchain.com API."""
    url = (
        f"https://api.blockchain.info/charts/{chart_name}"
        f"?timespan={timespan}&format=json&cors=true"
    )
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "quant/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            values = data.get("values", [])
            if not values:
                return pd.DataFrame()
            rows = []
            for v in values:
                ts = datetime.fromtimestamp(v["x"], tz=timezone.utc)
                rows.append({"timestamp": ts, "value": float(v["y"])})
            return pd.DataFrame(rows)
        except Exception as e:
            print(f"    Attempt {attempt+1}: {e}")
            time.sleep(2 * (attempt + 1))
    return pd.DataFrame()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_data = {}
    for metric_name, chart_id in BLOCKCHAIN_CHARTS.items():
        print(f"Fetching {metric_name}...", end=" ", flush=True)
        df = fetch_blockchain_chart(chart_id, "2years")
        if len(df) > 0:
            all_data[metric_name] = df
            print(f"{len(df)} days ({df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()})")
        else:
            print("NO DATA")
        time.sleep(1)  # rate limit

    # Merge all into one wide DataFrame
    if all_data:
        merged = None
        for name, df in all_data.items():
            df = df.rename(columns={"value": name}).set_index("timestamp")
            if merged is None:
                merged = df
            else:
                merged = merged.join(df, how="outer")

        merged = merged.sort_index().ffill()
        out_path = OUT_DIR / "btc_onchain_daily.csv"
        merged.to_csv(out_path)
        print(f"\nSaved {len(merged)} rows × {len(merged.columns)} metrics to {out_path}")
    else:
        print("\nNo data fetched.")


if __name__ == "__main__":
    main()
