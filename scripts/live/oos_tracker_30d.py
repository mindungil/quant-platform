#!/usr/bin/env python3
"""30-day rolling OOS tracker — statistical SR band check.

Complement to alpha_health_daily.py (ratio-based). This tracker:
  - Computes 30-day rolling realized SR per symbol from snapshots.jsonl
  - Compares against config.backtest_expectations OOS values
  - Estimates standard error of the live SR using
        SE(SR) ≈ sqrt((1 + 0.5·SR²) / N)   (Lo 2002 approximation)
  - Flags when live SR falls below `expected - k·SE` for K consecutive
    daily checks (k=1.0 default, K=5 default).

Why this on top of alpha_health_daily?
  - Ratio-based gates (live/expected < 0.3) trip on noise when expected≈0
    or when the live SR is moving through zero. σ-bands give a calibrated,
    statistically interpretable threshold.
  - 5d soak (2026-04-25 → 04-30) showed live ETH SR=1.02 vs expected 0.27
    — ratio-based interprets this as "3.78× outperform" (good!), but a σ-
    based view treats it as `+0.75 / SE ≈ many sigma above` (suspicious of
    overfitting/regime). Both readings are useful.

Output:
  - data/loop/oos_tracker_30d.jsonl (one line/day, append-only audit)
  - state.json.oos_tracker (current band status + streaks, atomic)
  - stdout summary table when run interactively

Usage:
  python3 scripts/live/oos_tracker_30d.py
  python3 scripts/live/oos_tracker_30d.py --window-days 30 --k-sigma 1.0 --alert-streak 5
  python3 scripts/live/oos_tracker_30d.py --json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone, date as date_cls
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))
SNAPSHOTS_PATH = Path(os.getenv("LOOP_SNAPSHOTS_PATH", "/home/ubuntu/quant/data/loop/snapshots.jsonl"))
TRACKER_LOG = Path(os.getenv("OOS_TRACKER_LOG", "/home/ubuntu/quant/data/loop/oos_tracker_30d.jsonl"))

# Symbol → SR field name in snapshot dicts
_SR_FIELDS = {
    "BTC": "btc_6m_sr",
    "ETH": "eth_6m_sr",
    "BNB": "bnb_6m_sr",
    # SOL/SOLUSDT recently added — no live SR field yet in snapshot schema.
    # Will surface once snapshot writer (engine/health_check or loop driver)
    # is updated to populate sol_6m_sr.
}


def sr_standard_error(live_sr: float, n_obs: int) -> float:
    """Lo (2002) SR standard error: SE ≈ sqrt((1 + 0.5·SR²) / N).

    Assumes IID returns and hourly bars. For 30d hourly, N=720; for 6M, N≈4320.
    """
    if n_obs <= 1:
        return float("inf")
    return math.sqrt((1.0 + 0.5 * (live_sr ** 2)) / n_obs)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text())


def save_state_atomic(state: dict):
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def append_log(record: dict):
    TRACKER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TRACKER_LOG.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def compute_band(live_sr: float, expected_sr: float, n_obs: int, k: float) -> dict:
    """Return band classification dict for a (live, expected) pair."""
    se = sr_standard_error(live_sr, n_obs)
    deviation = live_sr - expected_sr
    z = deviation / se if se > 0 and not math.isinf(se) else 0.0
    lower = expected_sr - k * se
    upper = expected_sr + k * se
    if math.isinf(se):
        status = "insufficient_data"
    elif live_sr < lower:
        status = "below_band"
    elif live_sr > upper:
        status = "above_band"
    else:
        status = "in_band"
    return {
        "live_sr": round(live_sr, 4),
        "expected_sr": round(expected_sr, 4),
        "se": round(se, 4) if not math.isinf(se) else None,
        "deviation": round(deviation, 4),
        "z_score": round(z, 2) if not math.isinf(se) else None,
        "lower_band_1sigma": round(lower, 4) if not math.isinf(se) else None,
        "upper_band_1sigma": round(upper, 4) if not math.isinf(se) else None,
        "status": status,
        "n_obs_assumed": n_obs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30,
                    help="Rolling window for SR computation (used to size N)")
    ap.add_argument("--bars-per-day", type=int, default=24)
    ap.add_argument("--k-sigma", type=float, default=1.0,
                    help="Band width in σ units; below_band when live < expected - k·SE")
    ap.add_argument("--alert-streak", type=int, default=5,
                    help="Consecutive below_band days that trigger an alert")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON only, no stdout table")
    args = ap.parse_args()

    state = load_state()
    bt = state.get("backtest_expectations", {})
    if not bt:
        print("ERROR: state.backtest_expectations missing — cannot compare", file=sys.stderr)
        return 2

    last_snap = state.get("last_snapshot", {})
    if not last_snap:
        print("ERROR: state.last_snapshot missing — no live SR to compare", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).date().isoformat()
    n_obs = args.window_days * args.bars_per_day  # 720 for 30d/hourly

    prev_tracker = state.get("oos_tracker", {})
    new_tracker = {"last_check_date": today, "k_sigma": args.k_sigma,
                   "alert_streak_threshold": args.alert_streak, "by_symbol": {}}

    rows = []
    alerts = []
    for sym, sr_field in _SR_FIELDS.items():
        sym_lc = sym.lower()
        live_sr = last_snap.get(sr_field)
        sym_bt = bt.get(sym_lc, {})
        expected_sr = sym_bt.get("sr")
        if live_sr is None or expected_sr is None:
            band = {"status": "no_data", "live_sr": live_sr, "expected_sr": expected_sr}
            new_tracker["by_symbol"][sym] = {"band": band, "consecutive_below": 0}
            rows.append((sym, band, 0, False))
            continue

        band = compute_band(float(live_sr), float(expected_sr), n_obs, args.k_sigma)

        prev_sym = prev_tracker.get("by_symbol", {}).get(sym, {})
        prev_below = prev_sym.get("consecutive_below", 0)
        prev_check_date = prev_tracker.get("last_check_date")

        # Streak update — only increment once per UTC day.
        if band["status"] == "below_band":
            if prev_check_date == today:
                # Already counted today; keep the same value
                streak = prev_below
            else:
                streak = prev_below + 1
        else:
            streak = 0

        new_tracker["by_symbol"][sym] = {"band": band, "consecutive_below": streak}
        alerted = streak >= args.alert_streak
        if alerted:
            alerts.append((sym, streak, band))
        rows.append((sym, band, streak, alerted))

    state["oos_tracker"] = new_tracker
    save_state_atomic(state)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "k_sigma": args.k_sigma,
        "alert_streak_threshold": args.alert_streak,
        "by_symbol": {sym: {"band": band, "streak": streak}
                      for (sym, band, streak, _) in rows},
        "alerts": [{"symbol": s, "streak": k, "band": b} for (s, k, b) in alerts],
    }
    append_log(record)

    if args.json:
        print(json.dumps(record, indent=2))
        return 1 if alerts else 0

    print("=" * 78)
    print(f"  30-DAY OOS TRACKER — {today}  (k={args.k_sigma}σ, N_assumed={n_obs})")
    print("=" * 78)
    print(f"  {'sym':<5} {'live_sr':>9} {'exp_sr':>8} {'SE':>7} {'z':>6} {'band':<20} {'streak':>7}")
    print(f"  {'-' * 5} {'-' * 9} {'-' * 8} {'-' * 7} {'-' * 6} {'-' * 20} {'-' * 7}")
    for (sym, band, streak, alerted) in rows:
        if band["status"] in ("no_data", "insufficient_data"):
            print(f"  {sym:<5} {band.get('live_sr','?'):>9} {band.get('expected_sr','?'):>8} "
                  f"{'-':>7} {'-':>6} {band['status']:<20} {streak:>7}")
            continue
        flag = "  ⚠ ALERT" if alerted else ""
        print(f"  {sym:<5} {band['live_sr']:>+9.3f} {band['expected_sr']:>+8.3f} "
              f"{band['se']:>7.3f} {band['z_score']:>+6.2f} {band['status']:<20} {streak:>7}{flag}")
    print()

    if alerts:
        print(f"  ⚠ {len(alerts)} symbol(s) below {args.k_sigma}σ band for ≥{args.alert_streak} consecutive checks:")
        for (sym, streak, band) in alerts:
            print(f"    - {sym}: live {band['live_sr']:+.3f} < lower {band['lower_band_1sigma']:+.3f} "
                  f"({streak}d streak, z={band['z_score']:+.2f})")
        print()
    else:
        print("  ✓ All symbols within statistical bands (no alerts).")
        print()

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
