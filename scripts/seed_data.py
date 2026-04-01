#!/usr/bin/env python3
"""Seed realistic BTCUSDT candle data, trigger signal evaluation and agent decision.

Uses only stdlib so it can run on the host without installing extra packages.
Reads HOST_* env vars from .env / .env.example via the same dotenv loader
used by other operator scripts.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Minimal env loader (mirrors scripts/common.py but stdlib-only)
# ---------------------------------------------------------------------------

def _load_env() -> None:
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / ".env.example"):
        if not candidate.exists():
            continue
        for raw_line in candidate.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
        return


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"  ERROR {exc.code} {url}: {body}", file=sys.stderr)
        return {"_error": exc.code, "_detail": body}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        return {"_error": exc.code, "_detail": body}


def _wait_health(url: str, *, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except Exception:
            time.sleep(2)
    print(f"  WARNING: {url} did not become healthy within {timeout}s", file=sys.stderr)


# ---------------------------------------------------------------------------
# Candle generation
# ---------------------------------------------------------------------------

def _generate_candles(count: int, start: datetime, initial_price: float) -> list[dict]:
    """Generate realistic hourly BTCUSDT candles using a random walk."""
    candles: list[dict] = []
    price = initial_price

    for i in range(count):
        ts = start + timedelta(hours=i)

        # Random walk: ~0.5 % per candle
        change_pct = random.gauss(0.0, 0.005)
        close = price * (1 + change_pct)

        # Intra-candle high/low spread (0.1 % -- 0.8 %)
        spread = random.uniform(0.001, 0.008) * price
        high = max(price, close) + spread * random.uniform(0.3, 1.0)
        low = min(price, close) - spread * random.uniform(0.3, 1.0)

        # Ensure pydantic validators pass: high >= open, low <= open
        open_price = price
        high = max(high, open_price)
        low = min(low, open_price)

        volume = round(random.uniform(500, 3000), 2)

        candles.append({
            "timestamp": ts.isoformat(),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
        })

        # Next candle opens at previous close
        price = close

    return candles


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------

def _resolve_start(market_base: str) -> datetime:
    """Pick a start timestamp that is 2 days ago, but also after the last
    candle already stored so we never get a non_monotonic_timestamp rejection."""
    start = datetime.now(UTC) - timedelta(days=2)
    try:
        latest = _get_json(f"{market_base}/candles/BTCUSDT/latest")
        if "timestamp" in latest:
            existing = datetime.fromisoformat(latest["timestamp"])
            if existing >= start:
                start = existing + timedelta(hours=1)
    except Exception:
        pass
    return start


def main() -> None:
    _load_env()

    market_base = _env("HOST_MARKET_DATA_BASE_URL", "http://localhost:8001")
    signal_base = _env("HOST_SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    crypto_base = _env("HOST_CRYPTO_AGENT_BASE_URL", "http://localhost:8006")

    # --- Wait for services --------------------------------------------------
    print("[seed-data] waiting for services ...")
    for base in (market_base, signal_base, crypto_base):
        _wait_health(f"{base}/health")

    # --- Generate and ingest candles ----------------------------------------
    num_candles = 50
    start = _resolve_start(market_base)
    candles = _generate_candles(num_candles, start, initial_price=82000.0)

    print(f"[seed-data] ingesting {num_candles} BTCUSDT hourly candles starting {start.isoformat()} ...")
    accepted = 0
    errors = 0
    batch_size = 10

    for i, candle in enumerate(candles, 1):
        result = _post_json(f"{market_base}/candles/BTCUSDT", candle)
        if result.get("accepted"):
            accepted += 1
        elif "_error" in result:
            errors += 1
        else:
            accepted += 1  # response without explicit accepted field

        # Brief pause between batches so feature-store can compute
        if i % batch_size == 0:
            print(f"  ... {i}/{num_candles} ingested")
            time.sleep(1.0)

    print(f"[seed-data] candle ingestion done: {accepted} accepted, {errors} errors")

    # Allow feature pipeline a moment to finish computing
    time.sleep(2.0)

    # --- Signal evaluation --------------------------------------------------
    print("[seed-data] evaluating signal for BTCUSDT ...")
    signal = _post_json(f"{signal_base}/signals/evaluate/BTCUSDT", {})
    signal_score = signal.get("score") or signal.get("signal_score", "n/a")
    threshold_crossed = signal.get("threshold_crossed", False)
    print(f"  signal score={signal_score}  threshold_crossed={threshold_crossed}")

    # --- Agent decision -----------------------------------------------------
    print("[seed-data] running crypto-agent decision for BTCUSDT ...")
    decision = _post_json(f"{crypto_base}/decisions/run/BTCUSDT", {})
    action = decision.get("action", "n/a")
    print(f"  decision action={action}")

    # --- Summary ------------------------------------------------------------
    last_candle = candles[-1]
    print()
    print("=" * 60)
    print("  Seed Data Summary")
    print("=" * 60)
    print(f"  Candles ingested : {accepted} / {num_candles}")
    print(f"  Errors           : {errors}")
    print(f"  Price range      : {candles[0]['open']:.2f} -> {last_candle['close']:.2f}")
    print(f"  Time range       : {candles[0]['timestamp']}  ..  {last_candle['timestamp']}")
    print(f"  Signal score     : {signal_score}")
    print(f"  Threshold crossed: {threshold_crossed}")
    print(f"  Agent action     : {action}")
    if "_error" in signal:
        print(f"  Signal error     : {signal.get('_detail', '')}")
    if "_error" in decision:
        print(f"  Decision error   : {decision.get('_detail', '')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
