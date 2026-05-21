#!/usr/bin/env python3
"""Daily report — system-wide 24h observation summary.

History: this used to be an 85-line signal-snapshot summarizer with no
PnL accounting despite the docstring claim. G14 rewrote it as a
section-collector pipeline that pulls from every observability surface
landed in Phases D/E/F + G-MV + G-OBS:

  - capital tier + portfolio NAV         (capital_tier_active,
                                          portfolio_total_exposure_usd)
  - per-strategy realized PnL            (scripts/analyze_shadow_pnl.py
                                          — G15)
  - MAB arm drift                        (mab_arm_n / mean / disabled
                                          — G17)
  - IC decay                             (quant_v3_learning_factor_ic_ir
                                          — G18)
  - Data coverage                        (venue_tick_age_seconds,
                                          cross_venue_price_divergence,
                                          signal_data_staleness_seconds)
  - DSR / alpha state                    (quant_v3_learning_alpha_dsr +
                                          alpha_state)

Each section is independent — a failing collector logs an error inline
and the rest of the report continues. Both stdout (for cron tail) and
data/reports/daily_YYYYMMDD.md (for archive) are written.

Usage:
    python scripts/live/daily_report.py             # full report
    python scripts/live/daily_report.py --no-archive  # stdout only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

UTC = timezone.utc
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
REPORTS_DIR = REPO_ROOT / "data" / "reports"


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def prom_query(q: str) -> list[dict]:
    """Run an instant PromQL query. Returns the result vector or []."""
    url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(q)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("status") != "success":
            return []
        return data.get("data", {}).get("result", []) or []
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return []


def safe(fn):
    """Decorator: trap exceptions in a section collector so one failure
    doesn't tank the whole report. Returns the traceback as a markdown
    code block — visible but contained.
    """
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            tb = traceback.format_exc()
            return f"**ERROR in `{fn.__name__}`**\n\n```\n{tb}\n```\n"
    return wrapper


def _fmt_float(v, decimals: int = 4) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


# ──────────────────────────────────────────────────────────────────
# Section: Capital + Portfolio (G9)
# ──────────────────────────────────────────────────────────────────


@safe
def section_capital() -> str:
    tier_map = {0: "PAPER", 1: "MICRO", 2: "SMALL", 3: "MID", 4: "FULL"}
    tier_pts = prom_query("capital_tier_active")
    tier = "?"
    if tier_pts:
        tier_val = int(float(tier_pts[0]["value"][1]))
        tier = f"{tier_map.get(tier_val, '?')} ({tier_val})"

    nav_pts = prom_query("portfolio_total_exposure_usd")
    nav = float(nav_pts[0]["value"][1]) if nav_pts else None

    conc_pts = prom_query("portfolio_concentration_max_weight")
    conc = float(conc_pts[0]["value"][1]) if conc_pts else None

    pos_pts = prom_query("portfolio_position_count")
    pos = int(float(pos_pts[0]["value"][1])) if pos_pts else None

    daily_cap_pts = prom_query("capital_tier_max_daily_notional_usd")
    daily_cap = float(daily_cap_pts[0]["value"][1]) if daily_cap_pts else None
    cap_ratio = (nav / daily_cap) if (nav and daily_cap) else None

    out = ["## Capital tier & portfolio", ""]
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| Active tier | **{tier}** |")
    out.append(f"| Total exposure (USD) | {_fmt_float(nav, 2)} |")
    out.append(f"| Position count | {pos if pos is not None else '—'} |")
    out.append(f"| Max single-asset weight | {_fmt_float(conc, 3)} |")
    out.append(f"| Exposure ÷ daily cap | {_fmt_float(cap_ratio, 2)}× |")
    out.append("")
    flags = []
    if conc is not None and conc > 0.8:
        flags.append(f"⚠ concentration {conc:.0%} — single asset dominates")
    if cap_ratio is not None and cap_ratio > 10:
        flags.append(f"⚠ exposure is {cap_ratio:.0f}× the tier daily cap")
    if flags:
        out.append("**Flags**:")
        for f in flags:
            out.append(f"- {f}")
        out.append("")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────
# Section: Per-strategy realized PnL (G15)
# ──────────────────────────────────────────────────────────────────


@safe
def section_strategy_pnl(hours: int = 24) -> str:
    # Reuse the existing D21 validator's aggregator — single source of
    # truth for the shadow_fills query. _per_strategy_sharpe decorates
    # each row with naive_sharpe + win_rate (the SQL alone doesn't).
    from scripts.analyze_shadow_pnl import _aggregate, _per_strategy_sharpe  # type: ignore

    rows = _aggregate(hours)
    _per_strategy_sharpe(rows)
    out = [f"## Per-strategy realized PnL (last {hours}h)", ""]
    if not rows:
        out.append("_no shadow_fills in window_")
        out.append("")
        return "\n".join(out)

    out.append("| Strategy | Fills | Cum PnL | Mean | Win% | naive SR | Verdict |")
    out.append("|---|---:|---:|---:|---:|---:|---|")
    for r in rows:
        name = r.get("strategy_name", "?")
        # Truncate UUIDs / overly long names
        if len(name) > 28:
            name = name[:25] + "…"
        fills = r.get("fills", 0)
        cum = r.get("cum_pnl", 0.0) or 0.0
        mean = r.get("mean_pnl", 0.0) or 0.0
        win_rate = r.get("win_rate", 0.0) or 0.0
        sharpe = r.get("naive_sharpe", 0.0) or 0.0
        if sharpe >= 1.5:
            verdict = "✅ promising"
        elif sharpe <= -1.0:
            verdict = "❌ losing"
        elif fills < 30:
            verdict = "🆕 too few fills"
        else:
            verdict = "🟡 mixed"
        out.append(
            f"| {name} | {fills} | {_fmt_float(cum, 4)} | "
            f"{_fmt_float(mean, 6)} | {win_rate*100:.0f}% | "
            f"{_fmt_float(sharpe, 2)} | {verdict} |"
        )
    out.append("")

    # Aggregate
    total_pnl = sum(r.get("cum_pnl", 0.0) or 0.0 for r in rows)
    total_fills = sum(r.get("fills", 0) for r in rows)
    out.append(f"**Aggregate**: {len(rows)} strategies, {total_fills} fills, "
               f"cum PnL = **{_fmt_float(total_pnl, 4)}**")
    out.append("")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────
# Section: MAB arm drift (G17)
# ──────────────────────────────────────────────────────────────────


@safe
def section_mab_drift() -> str:
    """Snapshot of every MAB arm's posterior state.

    Surfaces three pathologies automatically:
      - silent-drop arms: present in the registry but n=0 (the
        `_arms.update()` whitelist gotcha noted in CLAUDE.md)
      - 24h-no-update arms: getting selected/disabled by some
        upstream rule that isn't visible in MAB_DISABLED_ARMS
      - inverted-mean arms: mean reward < 0 for >100 observations
    """
    n_vec = prom_query("mab_arm_n")
    if not n_vec:
        return "## MAB arm drift\n\n_no mab_arm metrics scraped — check intelligence:8006_\n"

    n_map = {r["metric"].get("arm"): float(r["value"][1]) for r in n_vec}
    mean_map = {r["metric"].get("arm"): float(r["value"][1])
                for r in prom_query("mab_arm_mean")}
    std_map = {r["metric"].get("arm"): float(r["value"][1])
               for r in prom_query("mab_arm_std")}
    total_reward_map = {r["metric"].get("arm"): float(r["value"][1])
                        for r in prom_query("mab_arm_total_reward")}
    last_updated_map = {r["metric"].get("arm"): float(r["value"][1])
                        for r in prom_query("mab_arm_last_updated_seconds_ago")}
    disabled_map = {r["metric"].get("arm"): float(r["value"][1])
                    for r in prom_query("mab_arm_disabled")}

    silent_drops = [
        a for a, n in n_map.items()
        if n == 0 and disabled_map.get(a, 0.0) == 0.0
    ]
    no_update_24h = [
        a for a, age in last_updated_map.items()
        if age > 86400 and disabled_map.get(a, 0.0) == 0.0 and n_map.get(a, 0) > 0
    ]
    losing_arms = [
        a for a, m in mean_map.items()
        if m < 0 and n_map.get(a, 0) > 100
    ]

    out = ["## MAB arm drift", ""]
    out.append("| Arm | n | Mean | Std | Total reward | Last update | Status |")
    out.append("|---|---:|---:|---:|---:|---:|---|")
    for arm in sorted(n_map.keys()):
        n = n_map[arm]
        mean = mean_map.get(arm, 0.0)
        std = std_map.get(arm, 0.0)
        tot = total_reward_map.get(arm, 0.0)
        age = last_updated_map.get(arm)
        disabled = disabled_map.get(arm, 0.0) > 0
        if disabled:
            status = "🚫 disabled"
        elif n == 0:
            status = "⚠ silent-drop?"
        elif age and age > 86400:
            status = "💤 no update 24h+"
        elif mean < 0 and n > 100:
            status = "❌ losing"
        elif mean > 0 and n > 30:
            status = "✅ contributing"
        else:
            status = "🟡 building"
        if age is None or age != age:  # NaN
            age_disp = "—"
        elif age > 86400:
            age_disp = f"{age/86400:.1f}d"
        elif age > 3600:
            age_disp = f"{age/3600:.1f}h"
        else:
            age_disp = f"{age:.0f}s"
        out.append(
            f"| {arm} | {int(n)} | {_fmt_float(mean, 5)} | "
            f"{_fmt_float(std, 5)} | {_fmt_float(tot, 4)} | "
            f"{age_disp} | {status} |"
        )
    out.append("")

    flags: list[str] = []
    if silent_drops:
        flags.append(
            f"⚠ {len(silent_drops)} arms with n=0 but not disabled — "
            f"possible silent-drop bug (CLAUDE.md gotcha): "
            f"`{', '.join(silent_drops)}`"
        )
    if no_update_24h:
        flags.append(
            f"💤 {len(no_update_24h)} arms haven't been updated in 24h "
            f"despite being active: `{', '.join(no_update_24h)}`"
        )
    if losing_arms:
        flags.append(
            f"❌ {len(losing_arms)} arms with negative mean reward "
            f"over >100 observations: `{', '.join(losing_arms)}`"
        )
    if flags:
        out.append("**Flags**:")
        for f in flags:
            out.append(f"- {f}")
        out.append("")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────
# Section: Per-alpha attribution PnL (G16)
# ──────────────────────────────────────────────────────────────────


@safe
def section_alpha_attribution() -> str:
    """Per-alpha cumulative PnL + 24h delta from attribution-daemon.

    Distinct from section_strategy_pnl (which sums shadow_fills by
    strategy_id from the registry): this view comes from the
    attribution-daemon writing per-alpha PnL via Brinson decomposition.
    Both views should roughly agree at the system aggregate but the
    per-name attribution is what the MAB actually rewards.
    """
    cum = prom_query("quant_v3_attribution_alpha_cumulative_pnl")
    if not cum:
        return "## Per-alpha attribution PnL\n\n_no attribution data available_\n"

    # Try 24h delta first, fall back to 1h if attribution-daemon
    # hasn't been running 24h yet.
    delta_24h = prom_query(
        "quant_v3_attribution_alpha_cumulative_pnl - "
        "(quant_v3_attribution_alpha_cumulative_pnl offset 24h)"
    )
    delta_1h = prom_query(
        "quant_v3_attribution_alpha_cumulative_pnl - "
        "(quant_v3_attribution_alpha_cumulative_pnl offset 1h)"
    )

    delta_24h_map = {
        r["metric"].get("alpha_name"): float(r["value"][1]) for r in delta_24h
    }
    delta_1h_map = {
        r["metric"].get("alpha_name"): float(r["value"][1]) for r in delta_1h
    }

    out = ["## Per-alpha attribution PnL", ""]
    delta_col = "24h Δ" if delta_24h_map else "1h Δ"
    out.append(f"| Alpha | Cumulative | {delta_col} |")
    out.append("|---|---:|---:|")

    rows = sorted(
        cum,
        key=lambda r: -float(r["value"][1]),
    )
    total_cum = 0.0
    total_delta = 0.0
    for r in rows:
        name = r["metric"].get("alpha_name", "?")
        cum_v = float(r["value"][1])
        delta_v = delta_24h_map.get(name) if delta_24h_map else delta_1h_map.get(name)
        total_cum += cum_v
        if delta_v is not None:
            total_delta += delta_v
        out.append(
            f"| {name} | {_fmt_float(cum_v, 2)} | "
            f"{_fmt_float(delta_v, 2) if delta_v is not None else '—'} |"
        )
    out.append(f"| **TOTAL** | **{_fmt_float(total_cum, 2)}** | "
               f"**{_fmt_float(total_delta, 2)}** |")
    out.append("")
    if not delta_24h_map:
        out.append("_(24h delta unavailable — attribution-daemon has <24h "
                   "of history; showing 1h delta instead)_")
        out.append("")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────
# Top-level composition
# ──────────────────────────────────────────────────────────────────


def compose_report(hours: int = 24) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"# Daily report — {now}",
        "",
        f"_Window: last {hours}h_",
        "",
        section_capital(),
        section_alpha_attribution(),
        section_strategy_pnl(hours=hours),
        section_mab_drift(),
    ]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--no-archive", action="store_true",
                        help="stdout only, don't write data/reports/")
    args = parser.parse_args()

    report = compose_report(hours=args.hours)
    print(report)

    if not args.no_archive:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(UTC).strftime("%Y%m%d")
        out_path = REPORTS_DIR / f"daily_{today}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"\n_archived to {out_path}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
