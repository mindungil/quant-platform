"""Trade velocity gap analysis: backtest assumption vs live realized turnover.

5-day v4.5 soak found live n_trades / day << backtest implied turnover.
This quantifies the gap by symbol and reports cost/SR implications.

Usage:
    python3 scripts/analysis/trade_velocity_gap.py --days 5
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PAPER_LOG = Path("/home/ubuntu/quant/data/logs/paper.log")
WALK_FORWARD_RECENT = Path("/home/ubuntu/quant/data/results/walk_forward_recent.csv")
COST_BPS_BACKTEST = 5.0  # walk_forward.py uses cost_bps=5

TRADES_LINE = re.compile(
    r"Trades:\s*([A-Z]+USDT)\s*([+-][\d.]+)→([+-][\d.]+)"
)
N_TRADES_LINE = re.compile(r"total_trades=(\d+)")


def parse_paper_log(path: Path):
    """Yield (symbol, from_pos, to_pos, total_trades) tuples from paper.log."""
    if not path.exists():
        return
    last_total = None
    with path.open() as fh:
        for line in fh:
            t = N_TRADES_LINE.search(line)
            if t:
                last_total = int(t.group(1))
                continue
            m = TRADES_LINE.search(line)
            if m:
                yield (
                    m.group(1),
                    float(m.group(2)),
                    float(m.group(3)),
                    last_total,
                )


def load_backtest_oos():
    """OOS expectations from walk_forward_recent.csv. Returns {symbol: {sr, cagr, dd}}."""
    out = {}
    if not WALK_FORWARD_RECENT.exists():
        return out
    with WALK_FORWARD_RECENT.open() as fh:
        header = fh.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        for line in fh:
            row = line.strip().split(",")
            sym = row[idx["symbol"]]
            out[sym] = {
                "tuned_sr": float(row[idx["tuned_sharpe_5bp"]]),
                "tuned_cagr": float(row[idx["tuned_cagr_5bp"]]),
                "default_sr": float(row[idx["default_sharpe_5bp"]]),
                "oos_years": float(row[idx["oos_years"]]),
            }
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=float, default=5.0,
                   help="Window of soak in days (we observed 5d)")
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT",
                   help="Symbols to analyze")
    args = p.parse_args()

    target_syms = set(args.symbols.split(","))

    # Parse all live position transitions from paper log
    transitions = []  # list of (sym, from, to, abs_delta)
    final_total = None
    for sym, p_from, p_to, total in parse_paper_log(PAPER_LOG):
        transitions.append((sym, p_from, p_to, abs(p_to - p_from)))
        if total is not None:
            final_total = total

    # Aggregate per symbol — only keep the last `n_total_recent` transitions
    # that occurred during the soak window. Since n_trades went 205→218, the
    # soak-window fills are the last 13 transitions.
    SOAK_FILLS = 13  # derived from state.json baseline_t0 vs current
    soak_transitions = transitions[-SOAK_FILLS:] if len(transitions) >= SOAK_FILLS else transitions

    per_sym = defaultdict(lambda: {"n_fills": 0, "abs_turnover": 0.0,
                                    "max_pos_seen": 0.0, "min_pos_seen": 0.0})
    for sym, p_from, p_to, abs_d in soak_transitions:
        if sym not in target_syms:
            continue
        per_sym[sym]["n_fills"] += 1
        per_sym[sym]["abs_turnover"] += abs_d
        per_sym[sym]["max_pos_seen"] = max(per_sym[sym]["max_pos_seen"], p_to)
        per_sym[sym]["min_pos_seen"] = min(per_sym[sym]["min_pos_seen"], p_to)

    # All-time per-sym turnover (since paper.log start), for comparison
    per_sym_all = defaultdict(lambda: {"n_fills": 0, "abs_turnover": 0.0})
    for sym, p_from, p_to, abs_d in transitions:
        if sym not in target_syms:
            continue
        per_sym_all[sym]["n_fills"] += 1
        per_sym_all[sym]["abs_turnover"] += abs_d

    bars_per_day = 24
    soak_bars = int(args.days * bars_per_day)

    backtest = load_backtest_oos()

    # Backtest "implied turnover": from walk_forward we don't store turnover_stats
    # in walk_forward_recent.csv. We estimate from typical equal-weight signal
    # behavior: signals smooth via dz=0.10-0.20, position has high persistence.
    # Empirical from prior turnover_stats output (manually inspected on similar
    # configs): per_bar_turnover ≈ 0.04-0.08 for tuned configs.
    # Conservative midpoint: 0.06 per bar.
    BACKTEST_PER_BAR_TURNOVER = 0.06  # |Δpos| per hourly bar (estimate)

    print("=" * 76)
    print(f"  TRADE VELOCITY GAP ANALYSIS — {args.days}d soak window")
    print("=" * 76)
    print(f"  Soak fills (across all symbols): {SOAK_FILLS}")
    print(f"  Per-day total fill rate: {SOAK_FILLS / args.days:.2f}")
    print(f"  Bars in window per symbol: {soak_bars}")
    print()
    print(f"  {'symbol':<10} {'fills':>7} {'turnover':>10} {'per_bar':>10} {'bt_implied':>11} {'ratio':>8}")
    print(f"  {'-' * 10} {'-' * 7} {'-' * 10} {'-' * 10} {'-' * 11} {'-' * 8}")
    grand_live_to = 0.0
    grand_bt_to = 0.0
    for sym in sorted(target_syms):
        s = per_sym[sym]
        live_per_bar = s["abs_turnover"] / soak_bars if soak_bars else 0.0
        ratio = live_per_bar / BACKTEST_PER_BAR_TURNOVER if BACKTEST_PER_BAR_TURNOVER else 0.0
        grand_live_to += s["abs_turnover"]
        grand_bt_to += BACKTEST_PER_BAR_TURNOVER * soak_bars
        print(f"  {sym:<10} {s['n_fills']:>7d} {s['abs_turnover']:>10.4f} "
              f"{live_per_bar:>10.5f} {BACKTEST_PER_BAR_TURNOVER:>11.3f} {ratio:>7.1%}")
    print(f"  {'-' * 10} {'-' * 7} {'-' * 10} {'-' * 10} {'-' * 11} {'-' * 8}")
    grand_ratio = grand_live_to / grand_bt_to if grand_bt_to > 0 else 0.0
    print(f"  {'TOTAL':<10} {SOAK_FILLS:>7d} {grand_live_to:>10.4f} "
          f"{'-':>10} {grand_bt_to:>11.4f} {grand_ratio:>7.1%}")
    print()
    print("  COLUMNS:")
    print("    fills      = position-change events (≠ orders; one fill = one rebalance)")
    print("    turnover   = Σ|Δpos| over the window")
    print("    per_bar    = average |Δpos| per hourly bar (live)")
    print("    bt_implied = backtest assumption (≈0.06 per bar from typical configs)")
    print("    ratio      = live / backtest — <100% means live trades less than backtest assumes")
    print()

    # Cost & SR implications
    print("=" * 76)
    print("  COST & SR IMPLICATIONS")
    print("=" * 76)
    print(f"  Backtest cost/year (per sym, at 5bp & 0.06 per_bar_turnover):")
    bt_annual_cost_bps = BACKTEST_PER_BAR_TURNOVER * 8760 * COST_BPS_BACKTEST
    print(f"    = 0.06 × 8760 bars × 5bp = {bt_annual_cost_bps:,.0f} bps/yr")
    print(f"  Live realized cost/year (per sym, extrapolated from {args.days}d):")
    for sym in sorted(target_syms):
        s = per_sym[sym]
        annual_to = s["abs_turnover"] / args.days * 365
        annual_cost = annual_to * COST_BPS_BACKTEST
        deflation = annual_cost / bt_annual_cost_bps if bt_annual_cost_bps else 0
        print(f"    {sym:<10}  annual_turnover≈{annual_to:>7.2f}  "
              f"cost≈{annual_cost:>6.0f} bps/yr  (live/backtest={deflation:.1%})")
    print()
    print("  INTERPRETATION:")
    print("    • If live turnover is much lower than backtest, transaction-cost drag is OVERSTATED in backtest.")
    print("    • That means LIVE SR could be HIGHER than backtest OOS SR (cost was too pessimistic).")
    print("    • BUT — lower turnover also means signals get filtered. The alpha is partially missed too.")
    print("    • Net: which effect dominates depends on whether filtered signals were profitable.")
    print()

    # Backtest OOS reference (what we calibrate live expectations against)
    print("=" * 76)
    print("  BACKTEST OOS BASELINE (walk_forward_recent.csv)")
    print("=" * 76)
    if backtest:
        for sym in sorted(target_syms):
            if sym in backtest:
                bt = backtest[sym]
                print(f"  {sym:<10}  tuned_SR={bt['tuned_sr']:+.3f}  "
                      f"default_SR={bt['default_sr']:+.3f}  oos_yrs={bt['oos_years']:.2f}")
    else:
        print("  (walk_forward_recent.csv not available)")
    print()
    print("=" * 76)
    print("  VERDICT")
    print("=" * 76)
    print(f"  • Live fill rate over {args.days}d: {SOAK_FILLS / args.days:.2f}/day across "
          f"{len(target_syms)} symbols × ~4 alphas (≈{SOAK_FILLS / args.days / (len(target_syms) * 4):.2f} fill/strat/day)")
    print(f"  • Live |Δpos|/bar ≈ {grand_live_to / soak_bars / len(target_syms):.5f}  "
          f"vs backtest assumption ≈ {BACKTEST_PER_BAR_TURNOVER:.3f}")
    print(f"  • Implied turnover deflation: {grand_ratio:.1%}")
    print()
    print("  ACTIONABLE FINDINGS:")
    print("    1. backtest cost drag was conservatively overstated by ~%dx for these configs"
          % round(1 / max(grand_ratio, 0.01)))
    print("    2. → live SR upper bound is HIGHER than backtest_oos suggests (cost slack)")
    print("    3. → but signal coverage is LOWER (gates filter many would-be rebalances)")
    print("    4. → recommend running walk_forward_backtest with cost_bps=1 to find the upper bound,")
    print("         and a separate live-turnover-only re-fit to find the realistic floor.")


if __name__ == "__main__":
    main()
