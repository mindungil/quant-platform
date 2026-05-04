#!/usr/bin/env python3
"""End-to-end latency check — bar close → signal → order → fill.

Three stages, each measured from independent timestamp sources so a
broken / skipped stage is immediately visible:

  stage 1: bar_close → signal_file_written
    = file_mtime(signals_*.json) − max(signal.timestamp in file)

  stage 2: signal_file_written → reconcile_started
    = first reconcile_log.ts after signal_file_written − file_mtime

  stage 3: order_submit → fill (when fills carry both)
    = fill.ts − fill.submit_ts (only present in execution_log entries
      that include both)

Reports p50 / p95 / max for each stage over the last N signal files.
Threshold defaults are conservative for an hourly-bar system; adjust if
running faster bars.

Usage:
  python3 scripts/live/latency_check.py
  python3 scripts/live/latency_check.py --last 50
  python3 scripts/live/latency_check.py --json
  python3 scripts/live/latency_check.py --push-telegram   # send if any p95 exceeds threshold
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SIGNALS_DIR = REPO_ROOT / "data" / "signals"
RECONCILE_DIR = REPO_ROOT / "data" / "logs" / "reconciliation"
EXEC_DIR = REPO_ROOT / "data" / "logs" / "execution"
VIRTUAL_HISTORY = REPO_ROOT / "data" / "virtual" / "history.jsonl"

# Default thresholds (seconds) — flag in report when p95 exceeds these
DEFAULT_THRESHOLDS = {
    "stage1_signal_gen_p95":   180.0,   # signal file written within 3min of bar
    "stage2_bridge_p95":       300.0,   # reconcile triggered within 5min of signal
    "stage3_exec_p95":           5.0,   # fill within 5s of submit (live)
}


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _stage1(signal_files: list[Path]) -> list[float]:
    """signal-generation latency per file: file mtime − max signal ts."""
    out: list[float] = []
    for fpath in signal_files:
        try:
            payload = json.loads(fpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list) or not payload:
            continue
        sig_ts = []
        for entry in payload:
            ts = entry.get("timestamp") if isinstance(entry, dict) else None
            if ts:
                try:
                    sig_ts.append(_parse_iso(ts))
                except ValueError:
                    pass
        if not sig_ts:
            continue
        bar_close = max(sig_ts)
        mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
        delta = (mtime - bar_close).total_seconds()
        if delta < 0:
            # mtime can predate signal.timestamp if signals are timestamped
            # for the bar they apply to (close of next bar). Use abs.
            delta = abs(delta)
        out.append(delta)
    return out


def _reconcile_entries() -> list[tuple[datetime, dict]]:
    """All reconcile entries across files, sorted by ts."""
    entries: list[tuple[datetime, dict]] = []
    if not RECONCILE_DIR.exists():
        return entries
    for fpath in sorted(RECONCILE_DIR.glob("*.jsonl")):
        for r in _read_jsonl(fpath):
            ts = r.get("ts")
            if not ts:
                continue
            try:
                entries.append((_parse_iso(ts), r))
            except ValueError:
                continue
    entries.sort(key=lambda x: x[0])
    return entries


def _stage2(signal_files: list[Path], recon: list[tuple[datetime, dict]]) -> list[float]:
    """signal_file_written → first reconcile after it."""
    out: list[float] = []
    if not recon:
        return out
    for fpath in signal_files:
        mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
        # binary-ish search: find first recon ts >= mtime
        nxt = next((r_ts for r_ts, _ in recon if r_ts >= mtime), None)
        if nxt is None:
            continue
        out.append((nxt - mtime).total_seconds())
    return out


def _stage3() -> list[float]:
    """submit→fill latency. Pulls from virtual history (fills) if it
    carries submit_ts, and from any live execution log that does too.
    """
    out: list[float] = []
    for r in _read_jsonl(VIRTUAL_HISTORY):
        if r.get("type") != "fill":
            continue
        sub = r.get("submit_ts") or r.get("placed_ts")
        ts = r.get("timestamp")
        if not sub or not ts:
            continue
        try:
            out.append((_parse_iso(ts) - _parse_iso(sub)).total_seconds())
        except ValueError:
            continue
    if EXEC_DIR.exists():
        for fpath in sorted(EXEC_DIR.glob("*.jsonl")):
            for r in _read_jsonl(fpath):
                sub = r.get("submit_ts") or r.get("placed_ts") or r.get("submitted_at")
                ts = r.get("filled_at") or r.get("ts") or r.get("timestamp")
                if not sub or not ts:
                    continue
                try:
                    out.append((_parse_iso(ts) - _parse_iso(sub)).total_seconds())
                except ValueError:
                    continue
    return out


def _stats(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0}
    s = sorted(samples)
    def pct(p: float) -> float:
        if not s:
            return 0.0
        idx = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
        return s[idx]
    return {
        "n": len(s),
        "min": s[0],
        "p50": statistics.median(s),
        "p95": pct(95),
        "max": s[-1],
        "mean": statistics.mean(s),
    }


def evaluate(report: dict, thresholds: dict) -> tuple[str, list[str]]:
    """Returns (overall_status, breach_reasons)."""
    breaches: list[str] = []
    for key, gate in thresholds.items():
        # key is e.g. stage1_signal_gen_p95 → strip _p95 to find stage
        prefix = key.rsplit("_", 1)[0]  # "stage1_signal_gen"
        stage_key = prefix.split("_", 1)[0]  # "stage1"
        s = (report.get(stage_key) or {})
        p95 = s.get("p95")
        if p95 is None:
            continue
        if p95 > gate:
            breaches.append(f"{prefix} p95={p95:.2f}s > gate {gate:.0f}s")
    if not breaches:
        return "PASS", []
    return "WARN", breaches


def render_text(report: dict, breaches: list[str], status: str) -> str:
    def fmt(s: dict) -> str:
        if s.get("n", 0) == 0:
            return "  n=0 (no samples)"
        return (f"  n={s['n']}  min={s['min']:.3f}s  p50={s['p50']:.3f}s  "
                f"p95={s['p95']:.3f}s  max={s['max']:.3f}s  mean={s['mean']:.3f}s")

    lines = [f"# Latency check — {status}"]
    lines += ["", "## stage 1: bar_close → signal_file_written",
              fmt(report["stage1"])]
    lines += ["", "## stage 2: signal_file → reconcile_started",
              fmt(report["stage2"])]
    lines += ["", "## stage 3: order_submit → fill",
              fmt(report["stage3"])]
    if breaches:
        lines += ["", "## Breaches"]
        for b in breaches:
            lines.append(f"  ⚠ {b}")
    return "\n".join(lines)


def push_to_telegram(report: dict, breaches: list[str], status: str) -> None:
    try:
        from shared.notifications.telegram import TelegramNotifier, AlertLevel
        notifier = TelegramNotifier()
        if not notifier.enabled:
            print("  → telegram: not configured, skip")
            return
        icon = AlertLevel.WARNING if status != "PASS" else AlertLevel.INFO
        lines = [f"{icon} <b>Latency: {status}</b>"]
        for st in ("stage1", "stage2", "stage3"):
            s = report.get(st) or {}
            if s.get("n", 0) > 0:
                lines.append(f"<code>{st}</code> n={s['n']} p50={s['p50']:.2f}s p95={s['p95']:.2f}s")
        if breaches:
            lines.append("")
            for b in breaches[:5]:
                lines.append(f"• {b}")
        notifier.send("\n".join(lines))
        print("  → telegram: sent")
    except Exception as e:
        print(f"  → telegram skipped ({type(e).__name__}: {e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end latency check")
    parser.add_argument("--last", type=int, default=50,
                        help="Number of most recent signal files to inspect (default 50)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--push-telegram", action="store_true",
                        help="Push to Telegram if any p95 exceeds its threshold")
    args = parser.parse_args()

    if not SIGNALS_DIR.exists():
        print(f"signals dir not found: {SIGNALS_DIR}", file=sys.stderr)
        return 2
    files = sorted(SIGNALS_DIR.glob("signals_*.json"))[-args.last:]
    recon = _reconcile_entries()
    s1 = _stage1(files)
    s2 = _stage2(files, recon)
    s3 = _stage3()

    report = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "n_signal_files": len(files),
        "n_reconcile_entries": len(recon),
        "stage1": _stats(s1),
        "stage2": _stats(s2),
        "stage3": _stats(s3),
        "thresholds": DEFAULT_THRESHOLDS,
    }
    status, breaches = evaluate(report, DEFAULT_THRESHOLDS)

    if args.json:
        report["status"] = status
        report["breaches"] = breaches
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report, breaches, status))

    if args.push_telegram and status != "PASS":
        push_to_telegram(report, breaches, status)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
