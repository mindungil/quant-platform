#!/usr/bin/env python3
"""Backfill sentiment_hourly with FNG 8-year history.

Creates hourly rows from daily FNG data (forward-filled to hourly).
This gives the feature engine 8 years of sentiment time-series.
"""
import os, sys
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "external-data-service"))

import pandas as pd
from datetime import timezone
from app.db.sentiment_repo import sentiment_repository as repo

fng = pd.read_csv(
    os.path.join(REPO_ROOT, "data", "external", "fear_greed.csv"),
    parse_dates=["timestamp"], index_col="timestamp",
)
fng_val = fng["fng_value"].astype(float)
if fng_val.index.tz is not None:
    fng_val.index = fng_val.index.tz_localize(None)

# Expand daily → hourly
hourly_idx = pd.date_range(fng_val.index.min(), fng_val.index.max(), freq="h")
fng_hourly = fng_val.reindex(hourly_idx).ffill().dropna()

print(f"FNG range: {fng_hourly.index.min()} → {fng_hourly.index.max()} ({len(fng_hourly)} hours)")

assets = ["BTC", "ETH", "SOL"]
batch_size = 500
inserted = 0

for asset in assets:
    for i in range(0, len(fng_hourly), batch_size):
        chunk = fng_hourly.iloc[i:i + batch_size]
        for ts, val in chunk.items():
            fng_norm = -(val - 50) / 50  # contrarian: fear→positive
            repo._store.execute(
                """
                INSERT INTO sentiment_hourly
                    (asset, timestamp, nlp_mean, nlp_count, total_items,
                     fng_value, composite_score)
                VALUES
                    (:asset, :ts, 0.0, 0, 0, :fng, :composite)
                ON CONFLICT (asset, timestamp) DO UPDATE SET
                    fng_value = EXCLUDED.fng_value,
                    composite_score = CASE
                        WHEN sentiment_hourly.nlp_count > 0 THEN sentiment_hourly.composite_score
                        ELSE EXCLUDED.composite_score
                    END
                """,
                {"asset": asset, "ts": ts.isoformat(), "fng": int(val), "composite": round(fng_norm * 0.25, 4)},
            )
            inserted += 1
        if (i + batch_size) % 5000 == 0:
            print(f"  {asset}: {min(i + batch_size, len(fng_hourly))}/{len(fng_hourly)}")

print(f"\nBackfilled {inserted} hourly rows across {len(assets)} assets")
print(f"Total hourly rows: {repo.hourly_count()}")
