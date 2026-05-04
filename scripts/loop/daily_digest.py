#!/usr/bin/env python3
"""Daily digest for the v4.5 5-day soak loop.

Compresses ~24 hourly snapshots into a single human-readable summary so
the user doesn't have to grep state.json + snapshots.jsonl by hand.

For a given UTC date, reports:
  - first/last paper, virtual, max_dd
  - delta vs t0 baseline and vs the previous day
  - any new anomaly_narrations recorded that day
  - per-symbol live SR and backtest gap (warn if |gap| ≥ 2 SR)
  - current soak verdict (in_progress/pass/fail)

Usage:
  python3 scripts/loop/daily_digest.py                 # today (UTC)
  python3 scripts/loop/daily_digest.py --date 2026-04-26
  python3 scripts/loop/daily_digest.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, date as date_cls, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "loop"))

STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))
SNAPSHOTS_PATH = Path(os.getenv("LOOP_SNAPSHOTS_PATH", "/home/ubuntu/quant/data/loop/snapshots.jsonl"))

_SR_LIVE_FIELDS = {
    "btc_30d_sr": ("BTC", "btc"),
    "eth_6m_sr":  ("ETH", "eth"),
    "bnb_6m_sr":  ("BNB", "bnb"),
}


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit(f"state file not found: {STATE_PATH}")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _load_snapshots() -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []
    out = []
    for line in SNAPSHOTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _snap_date(snap: dict) -> date_cls | None:
    ts = snap.get("ts")
    if not ts:
        return None
    try:
        return _parse_iso(ts).date()
    except ValueError:
        return None


def build_digest(state: dict, snaps: list[dict], target_date: date_cls) -> dict:
    today = [s for s in snaps if _snap_date(s) == target_date]
    yesterday = [s for s in snaps if _snap_date(s) == target_date - timedelta(days=1)]
    baseline = state.get("baseline_t0") or {}
    expectations = state.get("backtest_expectations") or {}

    digest: dict = {
        "date": target_date.isoformat(),
        "iters_today": len(today),
        "iters_yesterday": len(yesterday),
        "baseline_paper": baseline.get("paper_capital"),
        "baseline_virtual": baseline.get("virtual_equity"),
    }

    if not today:
        digest["status"] = "no_snapshots_for_date"
        return digest

    first, last = today[0], today[-1]
    base_paper = baseline.get("paper_capital")

    digest["paper"] = {
        "first": first.get("paper"),
        "last": last.get("paper"),
        "delta_intraday": (last.get("paper") or 0) - (first.get("paper") or 0),
        "delta_vs_t0": ((last.get("paper") or 0) - base_paper) if base_paper else None,
        "delta_vs_t0_pct": (((last.get("paper") or 0) - base_paper) / base_paper * 100)
            if base_paper else None,
    }
    digest["virtual"] = {
        "first": first.get("virtual"),
        "last": last.get("virtual"),
    }
    digest["max_dd"] = {
        "first": first.get("max_dd"),
        "last": last.get("max_dd"),
        "peak_today": max((s.get("max_dd") or 0) for s in today),
    }
    digest["daily_ret_diff_bps"] = {
        "last": last.get("daily_ret_diff_bps"),
        "abs_max_today": max((abs(s.get("daily_ret_diff_bps") or 0) for s in today), default=0),
    }
    digest["warn_symbols_last"] = last.get("warn_symbols") or []
    digest["n_trades_last"] = last.get("n_trades")

    # Yesterday delta — what *changed* from yesterday's last snapshot
    if yesterday:
        y_last = yesterday[-1]
        if y_last.get("paper") is not None and last.get("paper") is not None:
            digest["paper"]["delta_vs_yesterday"] = last["paper"] - y_last["paper"]
        if y_last.get("max_dd") is not None and last.get("max_dd") is not None:
            digest["max_dd"]["delta_vs_yesterday"] = last["max_dd"] - y_last["max_dd"]

    # Per-symbol SR + backtest gap
    sr_table = []
    for live_key, (sym, exp_key) in _SR_LIVE_FIELDS.items():
        live_sr = last.get(live_key)
        exp_sr = (expectations.get(exp_key) or {}).get("sr")
        if live_sr is None:
            continue
        row = {"symbol": sym, "live_sr": live_sr, "expected_sr": exp_sr}
        if exp_sr is not None:
            row["gap"] = float(live_sr) - float(exp_sr)
            row["warn"] = abs(row["gap"]) >= 2.0
        sr_table.append(row)
    digest["sr_vs_backtest"] = sr_table

    # Anomaly narrations recorded today
    narrations = state.get("anomaly_narrations") or []
    today_narrations = []
    for n in narrations:
        ts = n.get("ts")
        if not ts:
            continue
        try:
            if _parse_iso(ts).date() == target_date:
                today_narrations.append({
                    "iter": n.get("iter"),
                    "ts": ts,
                    "severity": n.get("severity"),
                    "observation": n.get("observation"),
                    "action_taken": n.get("action_taken"),
                })
        except ValueError:
            continue
    digest["narrations_today"] = today_narrations

    # Soak verdict (current)
    digest["soak_verdict"] = state.get("soak_verdict") or {"status": "in_progress (no verdict written)"}

    digest["status"] = "ok"
    return digest


def _fmt_money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def _fmt_pct(v) -> str:
    return f"{v*100:+.2f}%" if isinstance(v, (int, float)) else "—"


def _fmt_signed(v, suffix: str = "") -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:+.2f}{suffix}"


def render_text(d: dict) -> str:
    if d.get("status") == "no_snapshots_for_date":
        return f"[{d['date']}] no snapshots recorded for this UTC date"

    lines = [
        f"# Daily digest — {d['date']} (UTC)",
        f"  iters: {d['iters_today']} today, {d['iters_yesterday']} yesterday",
        "",
        f"## Paper / Virtual",
        f"  paper:   {_fmt_money(d['paper']['first'])} → {_fmt_money(d['paper']['last'])}  "
        f"intraday {_fmt_signed(d['paper']['delta_intraday'])}, "
        f"vs t0 {_fmt_signed(d['paper'].get('delta_vs_t0'))} ({_fmt_signed(d['paper'].get('delta_vs_t0_pct'), '%')})",
    ]
    if "delta_vs_yesterday" in d["paper"]:
        lines.append(f"  vs yesterday: {_fmt_signed(d['paper']['delta_vs_yesterday'])}")
    lines += [
        f"  virtual: {_fmt_money(d['virtual']['first'])} → {_fmt_money(d['virtual']['last'])}",
        f"  max_dd:  {(d['max_dd']['first'] or 0)*100:.1f}% → {(d['max_dd']['last'] or 0)*100:.1f}%  "
        f"(peak {(d['max_dd']['peak_today'] or 0)*100:.1f}%)",
        f"  daily-ret diff (last): {d['daily_ret_diff_bps']['last']:+.1f}bps  "
        f"(|max| today {d['daily_ret_diff_bps']['abs_max_today']:.1f}bps)",
        f"  warn_symbols (last): {', '.join(d['warn_symbols_last']) or '—'}",
        f"  n_trades (last): {d['n_trades_last']}",
    ]

    if d.get("sr_vs_backtest"):
        lines += ["", "## SR vs backtest"]
        for row in d["sr_vs_backtest"]:
            warn_tag = "  ⚠" if row.get("warn") else ""
            lines.append(
                f"  {row['symbol']}: live {_fmt_signed(row['live_sr'])}  "
                f"expected {_fmt_signed(row['expected_sr'])}  "
                f"gap {_fmt_signed(row.get('gap'))}{warn_tag}"
            )

    if d.get("narrations_today"):
        lines += ["", f"## Anomaly narrations today ({len(d['narrations_today'])})"]
        for n in d["narrations_today"]:
            lines.append(f"  [{n['severity'].upper()}] iter={n['iter']} {n['ts']}")
            lines.append(f"    obs: {n['observation']}")
            if n.get("action_taken"):
                lines.append(f"    action: {n['action_taken']}")
    else:
        lines += ["", "## Anomaly narrations today: none"]

    verdict = d.get("soak_verdict") or {}
    v_status = verdict.get("status", "unknown")
    lines += ["", f"## Soak verdict: {v_status}"]
    for r in (verdict.get("reasons") or [])[:5]:
        lines.append(f"  - {r}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily digest for v4.5 soak loop")
    parser.add_argument("--date", help="UTC date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    if args.date:
        target = date_cls.fromisoformat(args.date)
    else:
        target = datetime.now(timezone.utc).date()

    state = _load_state()
    snaps = _load_snapshots()
    digest = build_digest(state, snaps, target)

    if args.json:
        print(json.dumps(digest, indent=2, ensure_ascii=False))
    else:
        print(render_text(digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
