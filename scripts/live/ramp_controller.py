#!/usr/bin/env python3
"""paper → live ramp controller.

Manages staged capital ramp-up so the first live deployment doesn't go
0%→100% on day one. Reads / writes config/execution_mode.json["ramp"]
and tracks stage progression in data/loop/ramp_state.json.

Stages (defaults from config): [0.0, 0.1, 0.3, 0.6, 1.0].
At each stage the controller waits ≥ min_days_per_stage and verifies
promote gates (paper DD, drift, no critical alpha alerts). On gate fail
or explicit --rollback, it steps back one stage.

Commands:
  python3 scripts/live/ramp_controller.py status
  python3 scripts/live/ramp_controller.py check        # evaluate gates only, no change
  python3 scripts/live/ramp_controller.py promote      # auto-promote if gates pass
  python3 scripts/live/ramp_controller.py promote --force   # skip gates (manual override)
  python3 scripts/live/ramp_controller.py rollback     # step back one stage
  python3 scripts/live/ramp_controller.py set 0.3      # explicit factor (must be in stages)

Wiring:
  shared/execution/mode.py exposes get_ramp_factor() — hot-read from
  this same config file. Once the signal-to-order bridge multiplies
  position size by get_ramp_factor() (separate integration step), the
  controller is what the operator runs daily / weekly.

Why a separate controller:
  Keeping promote logic out of the trading hot path means a bad gate
  evaluation can't accidentally change live sizing mid-bar — the change
  is always a deliberate operator (or cron) action.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

CONFIG_PATH = Path(os.getenv("EXECUTION_MODE_CONFIG",
                             str(REPO_ROOT / "config" / "execution_mode.json")))
RAMP_STATE_PATH = Path(os.getenv("RAMP_STATE_PATH",
                                 str(REPO_ROOT / "data" / "loop" / "ramp_state.json")))
LOOP_STATE_PATH = Path(os.getenv("LOOP_STATE_PATH",
                                 str(REPO_ROOT / "data" / "loop" / "state.json")))
SNAPSHOTS_PATH = Path(os.getenv("LOOP_SNAPSHOTS_PATH",
                                str(REPO_ROOT / "data" / "loop" / "snapshots.jsonl")))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat().replace("+00:00", "Z")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"config not found: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text())


def _write_config(cfg: dict) -> None:
    cfg["last_updated"] = _now().date().isoformat()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_state() -> dict:
    if not RAMP_STATE_PATH.exists():
        return {}
    return json.loads(RAMP_STATE_PATH.read_text(encoding="utf-8"))


def _write_state(state: dict) -> None:
    RAMP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAMP_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")


def _read_loop_snapshots() -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []
    out = []
    for line in SNAPSHOTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _read_loop_state() -> dict:
    if not LOOP_STATE_PATH.exists():
        return {}
    return json.loads(LOOP_STATE_PATH.read_text(encoding="utf-8"))


@dataclass
class RampSnapshot:
    stages: list[float]
    factor: float
    stage_index: int
    min_days_per_stage: int
    gates: dict
    stage_started_at: str | None
    last_action: dict | None


def _snapshot() -> RampSnapshot:
    cfg = _read_config()
    ramp = cfg.get("ramp") or {}
    stages = [float(s) for s in (ramp.get("stages") or [0.0, 1.0])]
    factor = float(ramp.get("factor", 0.0))
    if factor not in stages:
        # Snap to nearest stage but record an anomaly note in state.last_action
        nearest = min(stages, key=lambda s: abs(s - factor))
        factor = nearest
    stage_index = stages.index(factor)
    state = _read_state()
    return RampSnapshot(
        stages=stages,
        factor=factor,
        stage_index=stage_index,
        min_days_per_stage=int(ramp.get("min_days_per_stage", 3)),
        gates=ramp.get("promote_gates") or {},
        stage_started_at=state.get("stage_started_at"),
        last_action=state.get("last_action"),
    )


def evaluate_gates(snap: RampSnapshot) -> tuple[bool, list[str]]:
    """Returns (pass, reasons). Empty stage_started_at means treat stage
    as just started (gate not yet evaluable).
    """
    reasons: list[str] = []
    if snap.stage_started_at is None:
        return False, ["stage_started_at not recorded — call --init or wait for first promote"]
    started = _parse_iso(snap.stage_started_at)
    days_in_stage = (_now() - started).total_seconds() / 86400.0
    if days_in_stage < snap.min_days_per_stage:
        reasons.append(
            f"in stage for {days_in_stage:.2f}d, need {snap.min_days_per_stage}d"
        )

    # DD / drift gates from snapshots within the current stage window
    snaps = _read_loop_snapshots()
    in_window = [s for s in snaps
                 if s.get("ts") and _parse_iso(s["ts"]) >= started]
    if in_window:
        max_dd_pct = max((s.get("max_dd") or 0) for s in in_window) * 100
        max_drift_bps = max((abs(s.get("daily_ret_diff_bps") or 0) for s in in_window),
                            default=0.0)
        gate_dd = float(snap.gates.get("max_paper_dd_pct", 5.0))
        gate_drift = float(snap.gates.get("max_drift_bps_abs", 100.0))
        if max_dd_pct > gate_dd:
            reasons.append(f"max_dd {max_dd_pct:.1f}% > gate {gate_dd}%")
        if max_drift_bps > gate_drift:
            reasons.append(f"|drift| {max_drift_bps:.1f}bps > gate {gate_drift}bps")
    else:
        reasons.append("no snapshots in current stage window — cannot evaluate DD/drift")

    # Critical alpha alerts (Phase 3 integration)
    if snap.gates.get("no_critical_alerts", True):
        loop_state = _read_loop_state()
        alpha_health = loop_state.get("alpha_health") or {}
        critical_syms = [
            sym for sym, e in alpha_health.items()
            if e.get("status") == "fail" and int(e.get("consecutive_fail_days", 0)) >= 14
        ]
        if critical_syms:
            reasons.append(f"alpha critical fail (≥14d streak): {','.join(critical_syms)}")

    # Live PnL gate: cumulative paper PnL within current stage must be ≥ threshold
    # (defaults to 0 = break-even). Blocks promote when alpha is *bleeding*
    # even if DD/drift would otherwise pass. Critical for honest progression
    # — DD-bound + drift-bound + losing money = bleed-out, not success.
    min_pnl_pct = snap.gates.get("min_live_pnl_pct")
    if min_pnl_pct is not None and in_window:
        first = in_window[0]
        last = in_window[-1]
        p0 = first.get("paper")
        p1 = last.get("paper")
        if isinstance(p0, (int, float)) and isinstance(p1, (int, float)) and p0 > 0:
            stage_pnl_pct = (p1 - p0) / p0 * 100
            if stage_pnl_pct < float(min_pnl_pct):
                reasons.append(
                    f"stage PnL {stage_pnl_pct:+.2f}% < gate {float(min_pnl_pct):+.2f}%"
                )

    return (len(reasons) == 0), reasons


def _set_factor(new_factor: float, action: str, reasons: list[str], force: bool = False) -> dict:
    cfg = _read_config()
    cfg.setdefault("ramp", {})["factor"] = new_factor
    _write_config(cfg)
    state = _read_state()
    state["stage_started_at"] = _now_iso()
    state["last_action"] = {
        "action": action,
        "ts": _now_iso(),
        "new_factor": new_factor,
        "force": force,
        "reasons": reasons,
    }
    _write_state(state)
    return state["last_action"]


def cmd_status() -> int:
    snap = _snapshot()
    print(f"factor={snap.factor:.2f} (stage {snap.stage_index+1}/{len(snap.stages)})")
    print(f"stages: {snap.stages}")
    print(f"min_days_per_stage: {snap.min_days_per_stage}")
    print(f"stage_started_at: {snap.stage_started_at or '—'}")
    if snap.last_action:
        a = snap.last_action
        print(f"last_action: {a['action']} → {a['new_factor']} at {a['ts']}"
              + (f" [FORCE]" if a.get("force") else ""))
        for r in (a.get("reasons") or []):
            print(f"  - {r}")
    return 0


def cmd_check() -> int:
    snap = _snapshot()
    ok, reasons = evaluate_gates(snap)
    print(f"gate evaluation: {'PASS' if ok else 'FAIL'}")
    for r in reasons:
        print(f"  - {r}")
    return 0 if ok else 2


def cmd_promote(force: bool = False) -> int:
    snap = _snapshot()
    if snap.stage_index + 1 >= len(snap.stages):
        print(f"already at top stage ({snap.factor:.2f}) — nothing to promote")
        return 0
    next_factor = snap.stages[snap.stage_index + 1]

    if not force:
        ok, reasons = evaluate_gates(snap)
        if not ok:
            print(f"promote BLOCKED — gates failed (use --force to override):")
            for r in reasons:
                print(f"  - {r}")
            return 3
        action_reasons = ["all gates passed"]
    else:
        action_reasons = ["force"]

    action = _set_factor(next_factor, "promote", action_reasons, force=force)
    print(f"PROMOTED: {snap.factor:.2f} → {next_factor:.2f}")
    if action.get("force"):
        print("  [FORCE — gates not evaluated]")
    return 0


def cmd_rollback() -> int:
    snap = _snapshot()
    if snap.stage_index == 0:
        print(f"already at bottom stage ({snap.factor:.2f}) — nothing to rollback")
        return 0
    prev_factor = snap.stages[snap.stage_index - 1]
    _set_factor(prev_factor, "rollback", ["manual"])
    print(f"ROLLBACK: {snap.factor:.2f} → {prev_factor:.2f}")
    return 0


def cmd_set(value: float) -> int:
    snap = _snapshot()
    if value not in snap.stages:
        print(f"ERROR: {value} not in declared stages {snap.stages}")
        return 1
    if value == snap.factor:
        print(f"already at {value:.2f} — no change")
        return 0
    direction = "promote" if value > snap.factor else "rollback"
    _set_factor(value, f"set ({direction})", ["explicit set"])
    print(f"SET: {snap.factor:.2f} → {value:.2f}")
    return 0


def cmd_init() -> int:
    """Mark the current stage as 'started now' — useful when initially
    bootstrapping or after manual config edit."""
    snap = _snapshot()
    state = _read_state()
    state["stage_started_at"] = _now_iso()
    state["last_action"] = {
        "action": "init",
        "ts": _now_iso(),
        "new_factor": snap.factor,
        "force": False,
        "reasons": ["bootstrap stage clock"],
    }
    _write_state(state)
    print(f"initialized stage_started_at={state['stage_started_at']} at factor={snap.factor:.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="paper→live ramp controller")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check")
    p_promote = sub.add_parser("promote")
    p_promote.add_argument("--force", action="store_true")
    sub.add_parser("rollback")
    p_set = sub.add_parser("set")
    p_set.add_argument("value", type=float)
    sub.add_parser("init")

    args = parser.parse_args()

    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "promote":
        return cmd_promote(force=args.force)
    if args.cmd == "rollback":
        return cmd_rollback()
    if args.cmd == "set":
        return cmd_set(args.value)
    if args.cmd == "init":
        return cmd_init()
    return 1


if __name__ == "__main__":
    sys.exit(main())
