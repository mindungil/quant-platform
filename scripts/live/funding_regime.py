#!/usr/bin/env python3
"""Funding-regime monitor.

funding_carry alpha (per v4_production.json: standalone BTC +0.64,
ETH +0.18, BNB +0.31 on 8yr 5bp) leans heavily on the funding-rate
distribution it was fitted to. If the live funding regime drifts
materially from that backtest period, the alpha's edge can erode
silently — this script detects that drift before it shows up in PnL.

Test:
  KS two-sample test on hourly funding rates:
    sample_recent = last `--lookback-days` days
    sample_baseline = all history before that
  H0: same distribution.
  reject (p < `--alpha`) → regime shift warn

Also reports practical statistics (mean / std, annualized) so the
operator can see *which way* it shifted (mean up = harder for
contrarian alpha; std up = bigger swings = more opportunity but more
noise).

Usage:
  python3 scripts/live/funding_regime.py
  python3 scripts/live/funding_regime.py --lookback-days 60 --json
  python3 scripts/live/funding_regime.py --push-telegram
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

try:
    from scipy.stats import ks_2samp  # noqa: E402
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


FUNDING_DIR = Path(os.getenv("FUNDING_DIR", str(REPO_ROOT / "data" / "funding")))
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]


def _load(sym: str) -> pd.Series | None:
    path = FUNDING_DIR / f"{sym}_funding.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    df = df.set_index("timestamp")
    return df["fundingRate"].astype(float).sort_index()


def _annualize(per_8h: float) -> float:
    """Funding settles 3x/day. Annualized % = rate * 3 * 365 * 100."""
    return per_8h * 3 * 365 * 100


def evaluate(sym: str, series: pd.Series, lookback_days: int, alpha: float) -> dict:
    if series is None or series.empty:
        return {"symbol": sym, "status": "no_data"}
    last_ts = series.index.max()
    cutoff = last_ts - timedelta(days=lookback_days)
    recent = series[series.index >= cutoff].dropna()
    baseline = series[series.index < cutoff].dropna()
    if len(recent) < 20 or len(baseline) < 100:
        return {
            "symbol": sym, "status": "insufficient_samples",
            "n_recent": len(recent), "n_baseline": len(baseline),
        }
    p_value = None
    ks_stat = None
    if _HAS_SCIPY:
        ks_stat, p_value = ks_2samp(recent.values, baseline.values)
        ks_stat = float(ks_stat)
        p_value = float(p_value)

    rec_mean = float(recent.mean())
    rec_std = float(recent.std())
    bl_mean = float(baseline.mean())
    bl_std = float(baseline.std())

    status = "pass"
    notes = []
    if p_value is not None and p_value < alpha:
        status = "shift_detected"
        notes.append(f"KS reject: p={p_value:.4g} < α={alpha}")
    if status == "pass" and abs(rec_mean - bl_mean) > 2 * (bl_std / max(len(baseline) ** 0.5, 1)):
        status = "mean_drift"
        notes.append("|mean diff| > 2 baseline SE")

    direction = ""
    if rec_mean > bl_mean * 1.5:
        direction = "harder_for_contrarian (funding higher → less to fade)"
    elif rec_mean < bl_mean * 0.5:
        direction = "favorable_for_contrarian (funding lower → opportunity to fade)"

    return {
        "symbol": sym,
        "status": status,
        "ks_stat": ks_stat,
        "ks_pvalue": p_value,
        "recent": {
            "n": len(recent), "mean_per_8h": rec_mean, "std_per_8h": rec_std,
            "annualized_mean_pct": _annualize(rec_mean),
            "annualized_std_pct": _annualize(rec_std),
        },
        "baseline": {
            "n": len(baseline), "mean_per_8h": bl_mean, "std_per_8h": bl_std,
            "annualized_mean_pct": _annualize(bl_mean),
            "annualized_std_pct": _annualize(bl_std),
        },
        "interpretation": direction,
        "notes": notes,
        "data_through": str(last_ts),
    }


def render_text(reports: list[dict], lookback_days: int, alpha: float) -> str:
    lines = [
        f"# Funding regime check — last {lookback_days}d vs baseline (KS α={alpha})",
    ]
    if not _HAS_SCIPY:
        lines.append("  (scipy not available — KS test skipped, mean-drift only)")
    lines.append("")
    for r in reports:
        if r["status"] in ("no_data", "insufficient_samples"):
            lines.append(f"  {r['symbol']}: {r['status']}")
            continue
        rec = r["recent"]; bl = r["baseline"]
        ks = f"KS={r['ks_stat']:.3f} p={r['ks_pvalue']:.4g}" if r['ks_pvalue'] is not None else "KS=n/a"
        lines.append(
            f"  {r['symbol']}: {r['status']:>16}  recent_ann={rec['annualized_mean_pct']:+.2f}%  "
            f"baseline_ann={bl['annualized_mean_pct']:+.2f}%  {ks}"
        )
        if r.get("interpretation"):
            lines.append(f"    → {r['interpretation']}")
        for n in r.get("notes", []):
            lines.append(f"    - {n}")
        lines.append(f"    data through {r['data_through']}")
    return "\n".join(lines)


def push_to_telegram(reports: list[dict], lookback_days: int) -> None:
    shifted = [r for r in reports if r.get("status") in ("shift_detected", "mean_drift")]
    if not shifted:
        return
    try:
        from shared.notifications.telegram import TelegramNotifier, AlertLevel
        notifier = TelegramNotifier()
        if not notifier.enabled:
            print("  → telegram: not configured, skip")
            return
        lines = [f"{AlertLevel.WARNING} <b>Funding regime shift</b>",
                 f"<i>last {lookback_days}d vs full history</i>", ""]
        for r in shifted:
            rec = r["recent"]; bl = r["baseline"]
            lines.append(
                f"<b>{r['symbol']}</b>: recent {rec['annualized_mean_pct']:+.1f}%/yr "
                f"vs baseline {bl['annualized_mean_pct']:+.1f}%/yr  "
                f"(p={r['ks_pvalue']:.3g})"
            )
            if r.get("interpretation"):
                lines.append(f"  ↳ {r['interpretation']}")
        notifier.send("\n".join(lines))
        print("  → telegram: sent")
    except Exception as e:
        print(f"  → telegram skipped ({type(e).__name__}: {e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Funding regime KS-test monitor")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--lookback-days", type=int, default=30,
                        help="Window for the recent sample (default 30d)")
    parser.add_argument("--alpha", type=float, default=0.05,
                        help="KS test significance level (default 0.05)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--push-telegram", action="store_true")
    args = parser.parse_args()

    reports = [evaluate(s, _load(s), args.lookback_days, args.alpha) for s in args.symbols]
    overall = "shift" if any(r.get("status") in ("shift_detected", "mean_drift") for r in reports) else "stable"

    if args.json:
        print(json.dumps({"overall": overall, "reports": reports,
                          "lookback_days": args.lookback_days, "alpha": args.alpha},
                         indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(reports, args.lookback_days, args.alpha))
        print(f"\noverall: {overall}")

    if args.push_telegram:
        push_to_telegram(reports, args.lookback_days)
    return 0 if overall == "stable" else 1


if __name__ == "__main__":
    sys.exit(main())
