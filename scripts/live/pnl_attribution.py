#!/usr/bin/env python3
"""Live PnL attribution — decompose paper/live PnL by alpha × symbol × day.

Reads `data/signals/signals_*.json` (the live signal stream) and
computes per-alpha contributions to portfolio PnL using the same
identity as shared/portfolio/attribution.py:

    contribution_alpha(t) = alpha_weight_alpha(t)
                          * alpha_position_alpha(t)
                          * ret(t→t+1)

  where ret is computed close-to-close on the signal `price` field for
  each symbol (signal cadence ~1h, so ret is per-signal-bar).

The alpha_position × alpha_weight product is, by construction, what the
ensemble *would have* used as target before vol-targeting/clip/live
guards. The residual between this and the actual `target_position`
shows up as "hedge_overlay" so row totals reconcile.

Outputs:
  by_alpha   — cumulative + Sharpe-ish stats per alpha
  by_symbol  — per-symbol contribution totals
  by_day     — daily PnL breakdown

Usage:
  python3 scripts/live/pnl_attribution.py
  python3 scripts/live/pnl_attribution.py --since 2026-04-25 --until 2026-04-26
  python3 scripts/live/pnl_attribution.py --json
  python3 scripts/live/pnl_attribution.py --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, date as date_cls
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SIGNALS_DIR = Path(os.getenv("SIGNALS_DIR", str(REPO_ROOT / "data" / "signals")))


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_signals(since: date_cls | None, until: date_cls | None,
                  symbol_filter: str | None) -> list[dict]:
    """Load and flatten all per-symbol signal entries within date range.

    Each input file is a list of per-symbol dicts; we tag with file_ts
    so duplicates within a bar collapse to one entry.
    """
    if not SIGNALS_DIR.exists():
        return []
    out: list[dict] = []
    for fpath in sorted(SIGNALS_DIR.glob("signals_*.json")):
        try:
            payload = json.loads(fpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            if not ts:
                continue
            try:
                dt = _parse_iso(ts)
            except ValueError:
                continue
            d = dt.date()
            if since and d < since:
                continue
            if until and d > until:
                continue
            if symbol_filter and entry.get("symbol") != symbol_filter:
                continue
            out.append(entry)
    return out


def _by_symbol_sorted(signals: list[dict]) -> dict[str, list[dict]]:
    """Group by symbol, sort by timestamp, dedup same-timestamp duplicates."""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        by_sym[s["symbol"]].append(s)
    for sym in by_sym:
        seen: set[str] = set()
        unique = []
        for s in sorted(by_sym[sym], key=lambda x: x["timestamp"]):
            ts = s["timestamp"]
            if ts in seen:
                continue
            seen.add(ts)
            unique.append(s)
        by_sym[sym] = unique
    return dict(by_sym)


def attribute(by_sym: dict[str, list[dict]]) -> dict:
    """Compute per-alpha / per-symbol / per-day contributions.

    Returns:
      {
        'by_alpha': [{alpha, total, hit_ratio, n_bars, mean_exposure}],
        'by_symbol': [{symbol, total, by_alpha: {...}}],
        'by_day': [{date, total, by_alpha: {...}}],
        'meta': {n_signals, n_symbols, n_alphas, residual_total},
      }
    """
    alpha_total: dict[str, float] = defaultdict(float)
    alpha_hits: dict[str, int] = defaultdict(int)
    alpha_bars: dict[str, int] = defaultdict(int)
    alpha_exposure_sum: dict[str, float] = defaultdict(float)

    sym_total: dict[str, float] = defaultdict(float)
    sym_alpha: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    day_total: dict[str, float] = defaultdict(float)
    day_alpha: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    residual_total = 0.0
    total_signals = 0
    seen_alphas: set[str] = set()

    for sym, series in by_sym.items():
        for i in range(len(series) - 1):
            cur = series[i]
            nxt = series[i + 1]
            p0 = cur.get("price")
            p1 = nxt.get("price")
            if not p0 or not p1 or p0 <= 0:
                continue
            ret = (p1 - p0) / p0
            d_iso = cur["timestamp"][:10]

            alpha_pos = cur.get("alpha_positions") or {}
            alpha_w = cur.get("alpha_weights") or {}
            target = cur.get("target_position")

            sum_attributed = 0.0
            for alpha, pos in alpha_pos.items():
                w = alpha_w.get(alpha)
                if w is None or pos is None:
                    continue
                contribution = float(w) * float(pos) * ret
                sum_attributed += float(w) * float(pos)  # for residual computation

                alpha_total[alpha] += contribution
                alpha_bars[alpha] += 1
                alpha_exposure_sum[alpha] += abs(float(pos))
                if contribution > 0:
                    alpha_hits[alpha] += 1
                seen_alphas.add(alpha)

                sym_total[sym] += contribution
                sym_alpha[sym][alpha] += contribution
                day_total[d_iso] += contribution
                day_alpha[d_iso][alpha] += contribution

            # Residual = (target - sum(w*pos)) * ret (vol targeting/clip/live_guard)
            if target is not None:
                residual = (float(target) - sum_attributed) * ret
                residual_total += residual
                sym_total[sym] += residual
                sym_alpha[sym]["_overlay"] += residual
                day_total[d_iso] += residual
                day_alpha[d_iso]["_overlay"] += residual

            total_signals += 1

    by_alpha_rows = []
    for alpha in sorted(seen_alphas):
        n = alpha_bars[alpha]
        by_alpha_rows.append({
            "alpha": alpha,
            "total_pnl": alpha_total[alpha],
            "n_bars": n,
            "hit_ratio": (alpha_hits[alpha] / n) if n else 0.0,
            "mean_abs_exposure": (alpha_exposure_sum[alpha] / n) if n else 0.0,
        })

    by_symbol_rows = []
    for sym in sorted(sym_total.keys()):
        by_symbol_rows.append({
            "symbol": sym,
            "total_pnl": sym_total[sym],
            "by_alpha": dict(sym_alpha[sym]),
        })

    by_day_rows = []
    for d in sorted(day_total.keys()):
        by_day_rows.append({
            "date": d,
            "total_pnl": day_total[d],
            "by_alpha": dict(day_alpha[d]),
        })

    return {
        "by_alpha": by_alpha_rows,
        "by_symbol": by_symbol_rows,
        "by_day": by_day_rows,
        "meta": {
            "n_signal_bars": total_signals,
            "n_symbols": len(by_sym),
            "n_alphas": len(seen_alphas),
            "residual_total": residual_total,
        },
    }


def render_text(report: dict) -> str:
    meta = report["meta"]
    lines = [
        f"# PnL attribution",
        f"  bars={meta['n_signal_bars']}  symbols={meta['n_symbols']}  alphas={meta['n_alphas']}",
        f"  residual (vol-target/clip/live_guard): {meta['residual_total']*100:+.4f}%",
        "",
        "## By alpha (returns ≈ multiplicative; expressed as sum of contributions)",
        f"  {'alpha':<24} {'total':>12} {'hit%':>7} {'n':>6} {'avg|pos|':>10}",
    ]
    for row in sorted(report["by_alpha"], key=lambda r: r["total_pnl"], reverse=True):
        lines.append(
            f"  {row['alpha']:<24} {row['total_pnl']*100:>+11.4f}% "
            f"{row['hit_ratio']*100:>6.1f}% {row['n_bars']:>6} "
            f"{row['mean_abs_exposure']:>10.3f}"
        )

    lines += ["", "## By symbol",
              f"  {'symbol':<10} {'total':>12}  per-alpha breakdown"]
    for row in sorted(report["by_symbol"], key=lambda r: r["total_pnl"], reverse=True):
        breakdown = ", ".join(
            f"{a}:{v*100:+.3f}%" for a, v in
            sorted(row["by_alpha"].items(), key=lambda kv: kv[1], reverse=True)
        )
        lines.append(f"  {row['symbol']:<10} {row['total_pnl']*100:>+11.4f}%  {breakdown}")

    if report["by_day"]:
        lines += ["", "## By day"]
        for row in report["by_day"]:
            lines.append(f"  {row['date']}: total {row['total_pnl']*100:>+10.4f}%")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live PnL attribution by alpha/symbol/day")
    parser.add_argument("--since", help="UTC date YYYY-MM-DD (inclusive)")
    parser.add_argument("--until", help="UTC date YYYY-MM-DD (inclusive)")
    parser.add_argument("--symbol", help="Filter to a single symbol (e.g. BTCUSDT)")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    since = date_cls.fromisoformat(args.since) if args.since else None
    until = date_cls.fromisoformat(args.until) if args.until else None

    signals = _load_signals(since, until, args.symbol)
    if not signals:
        print("[skip] no signals found in the requested window")
        return 0

    by_sym = _by_symbol_sorted(signals)
    report = attribute(by_sym)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
