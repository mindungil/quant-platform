#!/usr/bin/env python3
"""Drift feeder: portfolio PnL → signal-service live-drift monitor.

Runs on a cron (hourly for 1h-bar strategies) or as a long-lived worker.
Each tick:
  1. Pull active positions from portfolio-service
  2. Pull latest + previous-bar close from market-data
  3. Compute per-asset bar return = position_dir * (curr - prev) / prev
     (position_dir is +1/-1/0 from the target sign; we measure whether
     the strategy's directional bet paid off this bar)
  4. POST each realized bar return to signal-service
     `/signals/meta/drift/{asset}/observe`

Separating this from order-service keeps the observation loop asset-
centric (one realization per bar per asset) independent of how many
orders fired. Drift is a strategy-level statistic, not an order one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

import httpx


@dataclass
class AssetObservation:
    asset: str
    prev_close: float
    curr_close: float
    position: float
    bar_return: float


def compute_bar_return(position: float, prev_close: float, curr_close: float) -> float:
    """Directional bar return: position × relative move.

    position in [-1, 1] (signed fractional exposure).
    If position == 0 the observation is 0 (we held cash).
    """
    if prev_close <= 0:
        return 0.0
    raw = (curr_close - prev_close) / prev_close
    return position * raw


def build_observations(
    positions: dict[str, float],
    curr_prices: dict[str, float],
    prev_prices: dict[str, float],
) -> list[AssetObservation]:
    out: list[AssetObservation] = []
    for asset, pos in positions.items():
        curr = curr_prices.get(asset)
        prev = prev_prices.get(asset)
        if curr is None or prev is None or prev <= 0:
            continue
        out.append(
            AssetObservation(
                asset=asset,
                prev_close=float(prev),
                curr_close=float(curr),
                position=float(pos),
                bar_return=compute_bar_return(float(pos), float(prev), float(curr)),
            )
        )
    return out


class DriftFeederClient:
    def __init__(
        self,
        portfolio_url: str,
        market_data_url: str,
        signal_url: str,
        timeout: float = 5.0,
        user_id: str | None = None,
    ):
        self.portfolio_url = portfolio_url.rstrip("/")
        self.market_data_url = market_data_url.rstrip("/")
        self.signal_url = signal_url.rstrip("/")
        self.timeout = timeout
        self.user_id = user_id

    def _headers(self) -> dict:
        return {"X-User-Id": self.user_id} if self.user_id else {}

    def fetch_positions(self) -> dict[str, float]:
        r = httpx.get(
            f"{self.portfolio_url}/portfolio/positions",
            headers=self._headers(), timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        # Expected shape: list of {asset, position, ...} or dict
        if isinstance(data, dict):
            return {k: float(v) for k, v in data.items()}
        return {row["asset"]: float(row.get("position", 0.0)) for row in data}

    def fetch_prev_curr_closes(self, asset: str) -> tuple[float | None, float | None]:
        r = httpx.get(
            f"{self.market_data_url}/candles/{asset}",
            params={"interval": "1h", "limit": 2},
            timeout=self.timeout,
        )
        r.raise_for_status()
        candles = r.json()
        if not isinstance(candles, list) or len(candles) < 2:
            return None, None
        prev = float(candles[-2].get("close", 0))
        curr = float(candles[-1].get("close", 0))
        return prev, curr

    def post_observation(self, asset: str, bar_return: float) -> dict:
        r = httpx.post(
            f"{self.signal_url}/signals/meta/drift/{asset}/observe",
            json={"trade_return": bar_return},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def tick(self, assets: list[str]) -> list[dict]:
        positions = self.fetch_positions()
        curr_prices: dict[str, float] = {}
        prev_prices: dict[str, float] = {}
        for a in assets:
            if a not in positions:
                positions[a] = 0.0
            prev, curr = self.fetch_prev_curr_closes(a)
            if prev is not None and curr is not None:
                prev_prices[a], curr_prices[a] = prev, curr
        obs = build_observations(positions, curr_prices, prev_prices)
        results = []
        for o in obs:
            try:
                resp = self.post_observation(o.asset, o.bar_return)
                results.append({
                    "asset": o.asset,
                    "bar_return": o.bar_return,
                    "position": o.position,
                    "drift_level": resp.get("level"),
                })
            except Exception as exc:
                results.append({"asset": o.asset, "error": str(exc)[:200]})
        return results


def main():
    ap = argparse.ArgumentParser(description="Live-drift observation feeder")
    ap.add_argument("--assets", default="ETHUSDT,BTCUSDT,SOLUSDT")
    ap.add_argument("--portfolio-url", default=os.getenv("PORTFOLIO_URL", "http://portfolio-service:8000"))
    ap.add_argument("--market-data-url", default=os.getenv("MARKET_DATA_URL", "http://market-data:8000"))
    ap.add_argument("--signal-url", default=os.getenv("SIGNAL_URL", "http://signal-service:8000"))
    ap.add_argument("--user-id", default=os.getenv("DRIFT_USER_ID"))
    ap.add_argument("--interval", type=int, default=0,
                    help="Seconds between ticks. 0 = one-shot (cron mode)")
    args = ap.parse_args()

    client = DriftFeederClient(
        args.portfolio_url, args.market_data_url, args.signal_url,
        user_id=args.user_id,
    )
    assets = [a.strip() for a in args.assets.split(",") if a.strip()]

    while True:
        try:
            results = client.tick(assets)
            print(json.dumps({"ts": int(time.time()), "results": results}, default=str))
        except Exception as exc:
            print(json.dumps({"ts": int(time.time()), "error": str(exc)[:300]}), file=sys.stderr)
        if args.interval <= 0:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
