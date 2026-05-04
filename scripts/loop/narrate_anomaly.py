#!/usr/bin/env python3
"""Anomaly narration for the autonomous monitoring loop.

Given a snapshot diff (or an explicit anomaly observation), call llm-tools
/reasoning/generate to produce a structured narration:

    {
      "iter": int,
      "ts": ISO8601 UTC,
      "severity": "info" | "warn" | "critical",
      "observation": short factual statement,
      "hypothesis": likely cause (LLM-generated),
      "action_taken": what (if anything) the system did,
      "narration": full natural-language paragraph,
      "provider": which backend produced the narration
    }

Usage:
    # Append a narration entry to data/loop/state.json["anomaly_narrations"]
    python3 scripts/loop/narrate_anomaly.py \\
        --observation "BNB 6M EMA crossed warn (-0.02)" \\
        --severity warn \\
        --action "live_guard auto-halved position 0.054→0.027" \\
        --append

    # Auto-detect from latest snapshot vs baseline (no manual observation)
    python3 scripts/loop/narrate_anomaly.py --auto --append

The script falls back to llm-tools structured reasoning if no API key is set,
so it is safe to run unattended in any environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow lazy `from scripts.loop.notify_telegram import ...` and
# `from shared.notifications.telegram import ...` regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx

LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_BASE_URL", "http://localhost:8021")
STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))
SNAPSHOTS_PATH = Path(os.getenv("LOOP_SNAPSHOTS_PATH", "/home/ubuntu/quant/data/loop/snapshots.jsonl"))


def _load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit(f"state file not found: {STATE_PATH}")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _load_recent_snapshots(n: int = 10) -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []
    lines = SNAPSHOTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]


def _detect_severity(observation: str, snapshot: dict, baseline: dict) -> str:
    """Heuristic severity classifier — only used when --auto is set.

    Critical: paper DD > 20%, daily-ret diff > 200bps, paper drop > 5% from t0.
    Warn:     paper DD > 10%, daily-ret diff > 100bps, all-symbols negative SR.
    Info:     everything else.
    """
    obs_lower = observation.lower()
    if "critical" in obs_lower or "dd>20" in obs_lower or "halt" in obs_lower:
        return "critical"

    paper = snapshot.get("paper", 0) or 0
    base_paper = baseline.get("paper_capital", 0) or 0
    if base_paper > 0:
        change_pct = (paper - base_paper) / base_paper * 100
        if change_pct < -5:
            return "critical"
        if change_pct < -2:
            return "warn"

    max_dd = snapshot.get("max_dd", 0) or 0
    if max_dd > 0.20:
        return "critical"
    if max_dd > 0.10:
        return "warn"

    diff = abs(snapshot.get("daily_ret_diff_bps", 0) or 0)
    if diff > 200:
        return "critical"
    if diff > 100:
        return "warn"

    if "warn" in obs_lower or "halved" in obs_lower or "negative sr" in obs_lower:
        return "warn"
    return "info"


# Backtest gap thresholds — surface in narration when live diverges from
# the 8-yr backtest by more than this. SR is the headline metric (raw
# direction-of-edge sanity check). DD gap matters only when realized DD
# blows past the backtest envelope.
_SR_GAP_WARN = 2.0          # |live − backtest| ≥ 2.0 SR
_DD_GAP_WARN_FRAC = 0.05    # live DD ≥ backtest DD + 5pp

# Map snapshot SR field → (symbol, backtest key)
_SR_LIVE_FIELDS = {
    "btc_30d_sr": ("BTC", "btc"),
    "eth_6m_sr":  ("ETH", "eth"),
    "bnb_6m_sr":  ("BNB", "bnb"),
}


def _backtest_gaps(state: dict, snap: dict) -> list[str]:
    """Surface live vs backtest gaps that exceed the warn thresholds.

    Returns a list of short notes like 'BTC SR -3.31 vs +3.35 backtest
    (gap -6.66)'. Only emits items above threshold — no noise when live
    tracks expectations.
    """
    expectations = state.get("backtest_expectations") or {}
    # Skip meta keys (_doc, _production_inflated, etc.) — only real per-symbol entries
    expectations = {k: v for k, v in expectations.items() if not k.startswith("_")}
    if not expectations:
        return []
    notes: list[str] = []
    for live_key, (sym, exp_key) in _SR_LIVE_FIELDS.items():
        live_sr = snap.get(live_key)
        exp = expectations.get(exp_key) or {}
        exp_sr = exp.get("sr")
        if live_sr is None or exp_sr is None:
            continue
        gap = float(live_sr) - float(exp_sr)
        if abs(gap) >= _SR_GAP_WARN:
            # Round to 0.1 SR — sub-decimal jitter (e.g. -3.30→-3.31) is
            # noise that would otherwise defeat dedup and spam narrations.
            notes.append(
                f"{sym} SR {float(live_sr):+.1f} vs {float(exp_sr):+.1f} backtest "
                f"(gap {gap:+.1f})"
            )

    # DD gap: snapshot has portfolio-level max_dd; backtest has per-symbol
    # max_dd_pct. Compare against the *worst* (highest) symbol expectation,
    # since portfolio DD should not exceed the worst single-leg envelope by
    # much under half-Kelly sizing.
    live_dd = snap.get("max_dd")
    if live_dd is not None:
        worst_exp_dd = max(
            (float(v.get("max_dd_pct") or 0)
             for v in expectations.values()
             if isinstance(v, dict)),
            default=0.0,
        ) / 100.0
        if worst_exp_dd > 0 and float(live_dd) >= worst_exp_dd + _DD_GAP_WARN_FRAC:
            notes.append(
                f"DD {float(live_dd)*100:.1f}% vs worst-leg backtest {worst_exp_dd*100:.1f}% "
                f"(gap +{(float(live_dd)-worst_exp_dd)*100:.1f}pp)"
            )
    return notes


def _auto_observation(state: dict, recent: list[dict]) -> str | None:
    """Compose a one-line factual observation from latest snapshot vs baseline.

    Returns None if nothing notable changed (avoids spammy narration).
    """
    if not recent:
        return None
    snap = recent[-1]
    baseline = state.get("baseline_t0", {})

    notes: list[str] = []
    paper = snap.get("paper")
    base_paper = baseline.get("paper_capital")
    if paper and base_paper:
        change_pct = (paper - base_paper) / base_paper * 100
        if abs(change_pct) >= 1.0:
            notes.append(f"paper {change_pct:+.2f}% vs t0")

    max_dd = snap.get("max_dd")
    if max_dd and max_dd > 0.10:
        notes.append(f"max_dd {max_dd*100:.1f}%")

    warn_symbols = snap.get("warn_symbols") or []
    if warn_symbols:
        notes.append(f"warn_symbols={','.join(warn_symbols)}")

    diff = snap.get("daily_ret_diff_bps")
    if diff is not None and abs(diff) > 50:
        notes.append(f"daily_ret_diff={diff:+.1f}bps")

    # Backtest gap notes — only emit when live is materially off backtest.
    notes.extend(_backtest_gaps(state, snap))

    if not notes:
        return None
    iter_num = snap.get("iter", state.get("iteration_count", "?"))
    return f"iter={iter_num}: " + ", ".join(notes)


# Providers that produce real natural-language narration. Anything else
# (auto-reasoning, structured-reasoning, local-fallback) means the
# reasoning endpoint fell back to a *trading-signal template* — which is
# nonsense for anomaly narration. We replace those with a clean factual
# one-liner built from the snapshot itself.
_LLM_PROVIDERS = ("claude/oauth", "codex/oauth", "anthropic/api-key", "openai/api-key")


def _factual_narration(observation: str, severity: str, action: str | None,
                       state: dict, snap: dict) -> str:
    """Local factual narration when no real LLM is available.

    Plain-English statement of WHAT happened — no spurious trading
    recommendations. Safe to record without polluting state with templated
    matter.
    """
    iter_num = snap.get("iter", state.get("iteration_count", "?"))
    paper = snap.get("paper")
    base_paper = (state.get("baseline_t0") or {}).get("paper_capital")
    parts = [f"iter {iter_num}: {severity.upper()} — {observation}."]
    if paper is not None and base_paper:
        change_pct = (paper - base_paper) / base_paper * 100
        parts.append(f"Paper {paper:,.2f} ({change_pct:+.2f}% vs t0).")
    max_dd = snap.get("max_dd")
    if max_dd is not None:
        parts.append(f"max_dd {max_dd*100:.1f}%.")
    diff = snap.get("daily_ret_diff_bps")
    if diff is not None:
        parts.append(f"paper-virtual daily diff {diff:+.1f}bps.")
    if action:
        parts.append(f"Action: {action}.")
    return " ".join(parts)


def _call_reasoning(observation: str, severity: str, action: str | None,
                    state: dict, recent: list[dict]) -> tuple[str, str]:
    """Call llm-tools /reasoning/generate. Returns (narration, provider).

    Builds a synthetic ReasoningRequest using the anomaly fields. If a real
    LLM provider answers, returns its text. Otherwise (deterministic
    fallback) returns our own factual narration — the reasoning endpoint's
    structured fallback uses a trading-signal template that is misleading
    when applied to anomaly observation.
    """
    last_snap = recent[-1] if recent else {}
    baseline = state.get("baseline_t0", {})

    components: dict[str, float] = {}
    for k in ("daily_ret_diff_bps", "max_dd", "btc_30d_sr", "eth_6m_sr", "bnb_6m_sr"):
        v = last_snap.get(k)
        if isinstance(v, (int, float)):
            components[k] = float(v)

    severity_score_map = {"info": 0.0, "warn": -0.4, "critical": -0.8}
    signal_score = severity_score_map.get(severity, 0.0)

    external_context = {
        "observation": observation,
        "severity": severity,
        "action_taken": action or "none",
        "loop_iteration": state.get("iteration_count"),
        "deployment_version": (state.get("deployment") or {}).get("version"),
        "paper_capital": last_snap.get("paper"),
        "baseline_paper_capital": baseline.get("paper_capital"),
        "warn_symbols": last_snap.get("warn_symbols"),
    }

    payload = {
        "asset": "PORTFOLIO",
        "signal_score": signal_score,
        "strategy_name": "anomaly-narration",
        "memory_count": 0,
        "components": components,
        "regime": None,
        "formula_name": None,
        "external_context": external_context,
    }

    snap = recent[-1] if recent else {}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{LLM_GATEWAY_URL.rstrip('/')}/reasoning/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            provider = data.get("provider", "unknown")
            if provider in _LLM_PROVIDERS:
                return data.get("reasoning", ""), provider
            # Real LLM didn't answer — endpoint returned its trading-signal
            # template. Replace with a clean factual statement.
            return (
                _factual_narration(observation, severity, action, state, snap),
                f"factual-fallback (endpoint={provider})",
            )
    except Exception as exc:
        return (
            _factual_narration(observation, severity, action, state, snap),
            f"factual-fallback ({type(exc).__name__})",
        )


import re

_ITER_PREFIX_RE = re.compile(r"^iter=\d+:\s*")


def _strip_iter_prefix(observation: str | None) -> str:
    """Remove 'iter=N:' prefix so dedup ignores iteration counter changes."""
    return _ITER_PREFIX_RE.sub("", observation or "").strip()


def _is_duplicate(state: dict, observation: str, severity: str) -> bool:
    """Pre-LLM dedup check — true if the most recent narration already has
    the same observation+severity (iter prefix stripped). Lets us skip the
    /reasoning/generate call entirely in stable state.
    """
    narrations = state.get("anomaly_narrations") or []
    if not narrations:
        return False
    last = narrations[-1]
    return (_strip_iter_prefix(last.get("observation")) == _strip_iter_prefix(observation)
            and last.get("severity") == severity)


def _append_narration(entry: dict) -> bool:
    """Append narration to state.json; return True if written, False if dedup'd.

    Re-checks dedup at write time too — the pre-LLM check uses a snapshot
    of state, but a concurrent run could have appended in between.
    """
    state = _load_state()
    if _is_duplicate(state, entry.get("observation", ""), entry.get("severity", "")):
        return False
    narrations = state.get("anomaly_narrations") or []
    narrations.append(entry)
    state["anomaly_narrations"] = narrations
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate anomaly narration via llm-tools /reasoning/generate")
    parser.add_argument("--observation", help="Short factual statement of the anomaly")
    parser.add_argument("--severity", choices=["info", "warn", "critical"], help="Severity (auto-detected if omitted)")
    parser.add_argument("--action", help="Action taken by the system, if any", default=None)
    parser.add_argument("--auto", action="store_true",
                        help="Auto-derive observation from latest snapshot vs baseline")
    parser.add_argument("--append", action="store_true",
                        help="Append the result to state.json[\"anomaly_narrations\"]")
    parser.add_argument("--push-telegram", action="store_true",
                        help="If a new warn/critical narration is appended, push it to Telegram")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    args = parser.parse_args()

    state = _load_state()
    recent = _load_recent_snapshots(10)

    observation = args.observation
    if args.auto and not observation:
        observation = _auto_observation(state, recent)
        if not observation:
            if args.json:
                print(json.dumps({"skipped": "no notable changes"}))
            else:
                print("[skip] no notable changes — nothing to narrate")
            return 0
    if not observation:
        parser.error("must provide --observation or --auto")

    snap = recent[-1] if recent else {}
    severity = args.severity or _detect_severity(observation, snap, state.get("baseline_t0", {}))

    # Pre-LLM dedup: if the previous narration already matches this
    # observation+severity, skip the /reasoning/generate call entirely.
    # Saves an HTTP round-trip per iter when state is stable. Only short-
    # circuits when we're going to --append anyway; otherwise the user
    # explicitly wants the narration text.
    iter_num = snap.get("iter", state.get("iteration_count"))
    if args.append and _is_duplicate(state, observation, severity):
        entry = {
            "iter": iter_num,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "severity": severity,
            "observation": observation,
            "action_taken": args.action,
            "narration": "[skipped — duplicate of previous]",
            "provider": "dedup-skip",
        }
        if args.json:
            out = dict(entry)
            out["dedup_skipped"] = True
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            print(f"[{severity.upper()}] iter={iter_num} via dedup-skip")
            print(f"  observation: {observation}")
            print(f"  → dedup: identical observation+severity as previous entry, LLM call skipped")
        return 0

    narration, provider = _call_reasoning(observation, severity, args.action, state, recent)

    entry = {
        "iter": iter_num,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "severity": severity,
        "observation": observation,
        "action_taken": args.action,
        "narration": narration,
        "provider": provider,
    }

    written = True
    if args.append:
        written = _append_narration(entry)

    if args.json:
        out = dict(entry)
        out["dedup_skipped"] = (args.append and not written)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"[{severity.upper()}] iter={iter_num} via {provider}")
        print(f"  observation: {observation}")
        if args.action:
            print(f"  action:      {args.action}")
        print(f"  narration:   {narration}")
        if args.append:
            if written:
                print(f"  → appended to {STATE_PATH}")
            else:
                print(f"  → dedup: identical observation+severity as previous entry, skipped")

    # Telegram push: only when we actually appended a new warn/critical entry.
    # Lazy import keeps the script usable in environments without the
    # notifier on PYTHONPATH.
    if args.push_telegram and args.append and written and severity in ("warn", "critical"):
        try:
            from scripts.loop.notify_telegram import push_from_narration  # type: ignore
            from shared.notifications.telegram import TelegramNotifier  # type: ignore
            push_from_narration(TelegramNotifier(), dry_run=False)
        except Exception as exc:  # never let notification failure mask narration result
            print(f"  → telegram push skipped ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
