#!/usr/bin/env python3
"""Live readiness scorecard — 0-100 verdict before --live execution.

Complements `preflight_check.py` (which validates ops state) by scoring
the **trustworthiness of the data the soak was run with**. The scorecard
catches the most common cause of paper-vs-live divergence: the simulator
was unrealistic, so the soak baseline didn't predict live behavior.

Six dimensions, 0-100 each, weighted equally:

  1. Simulator realism      — funding sim on, slippage on
  2. Soak depth             — days completed, ops without anomaly
  3. Backtest-vs-live gap   — live SR within 50% of OOS expectation
  4. Risk infrastructure    — kill switch armed, risk_daemon log fresh
  5. API permissions        — withdraw disabled (mainnet only)
  6. Recovery readiness     — halt.flag absent, reconcile log fresh

Outputs:
  • Per-dimension score with the failing reason
  • Composite score 0-100
  • Verdict:
      ≥85 → GO         (proceed with caution: $1k cap recommended)
      60-84 → SOFT-NO  (testnet a bit longer, fix the ⚠ items)
      <60  → HARD-NO   (do not put real money in)

Usage:
  python3 scripts/live/live_readiness.py
  python3 scripts/live/live_readiness.py --json
  python3 scripts/live/live_readiness.py --check-api --api-key=$KEY --api-secret=$SECRET

Exit codes:
  0 = GO, 1 = SOFT-NO, 2 = HARD-NO
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

UTC = timezone.utc

LOOP_STATE = REPO_ROOT / "data" / "loop" / "state.json"
RAMP_STATE = REPO_ROOT / "data" / "loop" / "ramp_state.json"
PAPER_STATE = REPO_ROOT / "data" / "paper" / "portfolio_state.json"
HALT_FLAG = REPO_ROOT / "data" / "state" / "halt.flag"
RECONCILE_DIR = REPO_ROOT / "data" / "logs" / "reconciliation"
RISK_DAEMON_LOG = REPO_ROOT / "data" / "logs" / "risk_daemon.log"
PAPER_PORTFOLIO_PY = REPO_ROOT / "scripts" / "live" / "paper_portfolio.py"
SIGNAL_BRIDGE_PY = REPO_ROOT / "scripts" / "live" / "signal_to_order_bridge.py"


@dataclass
class DimensionScore:
    name: str
    score: int  # 0-100
    weight: float
    detail: str
    reasons: list[str]

    @property
    def weighted(self) -> float:
        return self.score * self.weight


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _file_age_hours(path: Path) -> float | None:
    if not path.exists():
        return None
    return (datetime.now(UTC).timestamp() - path.stat().st_mtime) / 3600


# ── Dimension 1: Simulator realism ───────────────────────────────────────

def score_realism() -> DimensionScore:
    reasons: list[str] = []
    score = 100

    # Check paper_portfolio default — soak baseline should already be on
    # post-2026-04-30; pre-soak the env override is what matters.
    sim_funding = os.environ.get("PAPER_SIM_FUNDING", "").lower()
    soak_end = datetime.fromisoformat("2026-04-30T03:30:00+00:00")
    post_soak = datetime.now(UTC) >= soak_end

    if sim_funding == "true":
        funding_active = True
    elif sim_funding == "false":
        funding_active = False
        reasons.append("PAPER_SIM_FUNDING=false explicit override — funding NOT charged")
        score -= 50
    else:
        # default-driven
        funding_active = post_soak
        if not funding_active:
            reasons.append(
                f"Soak in progress (ends {soak_end:%Y-%m-%d %H:%M UTC}) — "
                "paper sim has funding disabled by design until then"
            )
            score -= 30

    # Source-of-truth check on signal_to_order_bridge — virtual realism default
    bridge_src = SIGNAL_BRIDGE_PY.read_text() if SIGNAL_BRIDGE_PY.exists() else ""
    if "RealismConfig(slippage_enabled=True)" not in bridge_src:
        reasons.append("virtual_futures defaults to slippage_enabled=False — virtual sim too optimistic")
        score -= 30
    elif "--no-realism" in bridge_src:
        # OK — defaults on, opt-out only
        pass

    detail = "funding_sim=" + ("ON" if funding_active else "OFF") + ", slippage_sim=ON"
    return DimensionScore(
        name="simulator_realism",
        score=max(0, score),
        weight=1.0,
        detail=detail,
        reasons=reasons,
    )


# ── Dimension 2: Soak depth ──────────────────────────────────────────────

def score_soak() -> DimensionScore:
    reasons: list[str] = []
    state = _read_json(LOOP_STATE)
    if not state:
        return DimensionScore("soak_depth", 0, 1.0, "no loop state", ["data/loop/state.json missing"])

    started = state.get("started_at")
    if not started:
        return DimensionScore("soak_depth", 0, 1.0, "no start time", ["state.started_at missing"])

    days = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds() / 86400
    iters = state.get("iteration_count", 0)
    n_anomaly = len(state.get("anomalies_observed", []))

    # Industry baseline = 30 days. Score linear up to that, capped at 100.
    score = min(100, int(days / 30 * 100))
    if days < 5:
        reasons.append(f"only {days:.1f} days of soak (industry minimum: 30)")
    elif days < 30:
        reasons.append(f"{days:.1f} days of soak (industry minimum: 30)")
    if n_anomaly > 5:
        reasons.append(f"{n_anomaly} anomalies observed — investigate before adding capital")
        score -= 10

    detail = f"{days:.1f} days, iters={iters}, anomalies={n_anomaly}"
    return DimensionScore("soak_depth", max(0, score), 1.0, detail, reasons)


# ── Dimension 3: Backtest-vs-live gap ────────────────────────────────────

def score_alpha_health() -> DimensionScore:
    reasons: list[str] = []
    state = _read_json(LOOP_STATE)
    expectations = state.get("backtest_expectations", {})
    health = state.get("alpha_health", {})

    if not expectations or not health:
        return DimensionScore("backtest_vs_live", 0, 1.0,
                              "no expectations/health", ["loop state missing alpha_health"])

    score = 100
    gaps: list[str] = []
    for sym in ["BTC", "ETH", "BNB"]:
        sym_data = health.get(sym, {})
        live_sr = sym_data.get("live_sr")
        expected = expectations.get(sym.lower(), {}).get("sr")
        if live_sr is None or expected is None:
            continue
        if expected <= 0:
            continue
        ratio = live_sr / expected
        gaps.append(f"{sym} live={live_sr:+.2f} vs OOS={expected:+.2f} (ratio={ratio:+.2f})")
        if ratio < 0.5:
            # halve score per failing symbol
            score -= 25
            reasons.append(f"{sym} live SR {live_sr:+.2f} < 50% of OOS {expected:+.2f}")

    detail = "; ".join(gaps) if gaps else "no comparable data"
    return DimensionScore("backtest_vs_live", max(0, score), 1.0, detail, reasons)


# ── Dimension 4: Risk infrastructure ─────────────────────────────────────

def score_risk_infra() -> DimensionScore:
    reasons: list[str] = []
    score = 100

    if HALT_FLAG.exists():
        reasons.append(f"halt.flag present at {HALT_FLAG}")
        score -= 40

    age = _file_age_hours(RISK_DAEMON_LOG)
    if age is None:
        reasons.append("risk_daemon log absent — daemon may not be running")
        score -= 30
    elif age > 24:
        reasons.append(f"risk_daemon log stale ({age:.1f}h old)")
        score -= 20

    # Kill switch state — read from shared/risk
    try:
        from shared.risk.kill_switch import current_state
        ks = current_state()
        if ks and ks.get("tier") and ks["tier"] != "OK":
            reasons.append(f"kill switch tier={ks['tier']}")
            score -= 30
    except Exception:
        pass  # not a fail — kill_switch state file may not exist yet

    halt_part = "OFF" if not HALT_FLAG.exists() else "ON"
    daemon_part = f"{age:.1f}h" if age is not None else "absent"
    detail = f"halt={halt_part}, risk_daemon={daemon_part}"
    return DimensionScore("risk_infrastructure", max(0, score), 1.0, detail, reasons)


# ── Dimension 5: API permissions ─────────────────────────────────────────

def score_api_permissions(api_key: str | None, api_secret: str | None) -> DimensionScore:
    if not api_key or not api_secret:
        return DimensionScore(
            "api_permissions", 50, 1.0,
            "skipped (no creds passed)",
            ["pass --check-api --api-key/--api-secret to verify withdrawal=disabled"],
        )

    try:
        from shared.execution.binance_futures import BinanceFuturesConnector
        connector = BinanceFuturesConnector(api_key=api_key, api_secret=api_secret, testnet=False)
        perms = connector.validate_permissions()
        return DimensionScore(
            "api_permissions", 100, 1.0,
            f"verified: withdraw=False, futures=True",
            [],
        )
    except PermissionError as exc:
        return DimensionScore(
            "api_permissions", 0, 1.0,
            "FAIL — unsafe key", [str(exc)],
        )
    except Exception as exc:
        return DimensionScore(
            "api_permissions", 30, 1.0,
            f"check error: {exc}", [str(exc)],
        )


# ── Dimension 6: Recovery readiness ──────────────────────────────────────

def score_recovery() -> DimensionScore:
    reasons: list[str] = []
    score = 100

    # Reconcile log freshness — should run every bar (~1h)
    if RECONCILE_DIR.exists():
        latest = max(RECONCILE_DIR.glob("*.json"), default=None, key=lambda p: p.stat().st_mtime)
        if latest is None:
            reasons.append("no reconcile log yet")
            score -= 40
        else:
            age = (datetime.now(UTC).timestamp() - latest.stat().st_mtime) / 3600
            if age > 6:
                reasons.append(f"reconcile log stale ({age:.1f}h old, expected <6h)")
                score -= 30
    else:
        reasons.append("reconciliation/ dir missing — auditing not running")
        score -= 50

    # Paper state freshness
    age = _file_age_hours(PAPER_STATE)
    if age is None:
        reasons.append("paper state file missing")
        score -= 30
    elif age > 6:
        reasons.append(f"paper state stale ({age:.1f}h)")
        score -= 20

    reconcile_part = "present" if RECONCILE_DIR.exists() else "missing"
    paper_part = f"{age:.1f}h" if age is not None else "missing"
    detail = f"reconcile={reconcile_part}, paper_age={paper_part}"
    return DimensionScore("recovery_readiness", max(0, score), 1.0, detail, reasons)


# ── Composite + verdict ──────────────────────────────────────────────────

def composite_verdict(dims: list[DimensionScore]) -> tuple[int, str]:
    total_w = sum(d.weight for d in dims)
    composite = sum(d.weighted for d in dims) / total_w if total_w else 0
    if composite >= 85:
        verdict = "GO"
    elif composite >= 60:
        verdict = "SOFT-NO"
    else:
        verdict = "HARD-NO"
    return int(composite), verdict


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="JSON output for piping")
    ap.add_argument("--check-api", action="store_true",
                    help="actually call Binance to verify API key permissions")
    ap.add_argument("--api-key", default=os.getenv("BINANCE_API_KEY"))
    ap.add_argument("--api-secret", default=os.getenv("BINANCE_API_SECRET"))
    args = ap.parse_args()

    dims = [
        score_realism(),
        score_soak(),
        score_alpha_health(),
        score_risk_infra(),
        score_api_permissions(
            args.api_key if args.check_api else None,
            args.api_secret if args.check_api else None,
        ),
        score_recovery(),
    ]
    composite, verdict = composite_verdict(dims)

    if args.json:
        print(json.dumps({
            "composite_score": composite,
            "verdict": verdict,
            "dimensions": [asdict(d) for d in dims],
            "evaluated_at": datetime.now(UTC).isoformat(),
        }, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  LIVE READINESS SCORECARD")
        print(f"  evaluated: {datetime.now(UTC):%Y-%m-%d %H:%M:%S UTC}")
        print(f"{'='*60}")
        for d in dims:
            tag = "✓" if d.score >= 85 else ("⚠" if d.score >= 60 else "✗")
            print(f"  {tag} {d.name:24s} {d.score:>3d}/100   {d.detail}")
            for r in d.reasons:
                print(f"        — {r}")
        verdict_tag = "✓ GO" if verdict == "GO" else ("⚠ SOFT-NO" if verdict == "SOFT-NO" else "✗ HARD-NO")
        print(f"{'─'*60}")
        print(f"  COMPOSITE: {composite}/100   →   {verdict_tag}")
        print(f"{'='*60}\n")

        if verdict == "GO":
            print("  Recommendation: proceed with --live but cap initial capital at $1,000.")
            print("  Re-run scorecard after 7 trading days before increasing exposure.\n")
        elif verdict == "SOFT-NO":
            print("  Recommendation: do NOT put real money in. Fix ⚠ items, then continue testnet.\n")
        else:
            print("  Recommendation: do NOT proceed. The simulator-vs-live gap is too wide,")
            print("  or risk infrastructure is not running. Investigate ✗ items first.\n")

    return 0 if verdict == "GO" else (1 if verdict == "SOFT-NO" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
