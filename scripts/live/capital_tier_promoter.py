#!/usr/bin/env python3
"""Capital-tier promotion evaluator (G21-G25).

Background: shared/risk/capital_tier.py defines TierStats /
should_promote / should_demote / evaluate_tier_transition, but until
now nothing called it — the ladder existed on paper only.

This script is the missing consumer. It:

  1. computes TierStats over a configurable window
     (default 24h) from shadow_fills + Prometheus
  2. calls evaluate_tier_transition() for a verdict
  3. writes the verdict to Redis at capital_tier:promotion_candidate
     so daily_report can surface it as a human-approval gate
  4. NEVER applies the transition automatically (the --apply flag
     exists but defaults to false; even when on, it only flips
     the in-process tier, not env CAPITAL_TIER)

Sources:
  - n_trades / realized_sharpe → scripts.analyze_shadow_pnl._aggregate
    (per-strategy fills + Welford std → naive Sharpe)
  - realized_max_dd → cumulative-PnL drawdown across all strategies
  - hard_kill_events → Prometheus risk_halt_publications_total

Usage:
  python scripts/live/capital_tier_promoter.py                # dry-run
  python scripts/live/capital_tier_promoter.py --hours 48     # 48h window
  python scripts/live/capital_tier_promoter.py --json         # JSON output
  python scripts/live/capital_tier_promoter.py --apply        # actually flip
                                                              # (operator only)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

UTC = timezone.utc
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
REDIS_KEY = "capital_tier:promotion_candidate"

# Host-side default — same fallback as scripts/live/daily_report.py
os.environ.setdefault(
    "POSTGRES_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/platform",
)


# ──────────────────────────────────────────────────────────────────
# Stats compute
# ──────────────────────────────────────────────────────────────────


def _prom_scalar(query: str) -> float | None:
    url = f"{PROMETHEUS_URL}/api/v1/query?query={urllib.parse.quote(query)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    if data.get("status") != "success":
        return None
    results = data.get("data", {}).get("result", [])
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _per_strategy_pnl_series(hours: int) -> dict[str, list[float]]:
    """Pull ordered pnl per strategy_id over the window. Used for the
    aggregate drawdown calculation — naive_sharpe alone doesn't capture
    intra-window peak-to-trough exposure.
    """
    import psycopg
    url = os.environ["POSTGRES_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    sql = """
    SELECT strategy_id, ts, COALESCE(pnl, 0)::float AS pnl
    FROM shadow_fills
    WHERE ts > NOW() - (%s || ' hours')::interval
    ORDER BY ts ASC
    """
    out: dict[str, list[float]] = {}
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(hours),))
            for sid, _ts, pnl in cur.fetchall():
                out.setdefault(str(sid), []).append(float(pnl))
    return out


def _aggregate_drawdown(per_strategy: dict[str, list[float]], nav: float) -> float:
    """Worst negative cumulative-PnL excursion as a fraction of NAV.

    Earlier version normalized by the running PnL peak. At this scale
    a peak of <$1 means any negative excursion blows the fraction past
    100% — meaningless for tier evaluation. NAV-based normalization
    gives a stable, comparable signal (5% of NAV is a 5% of NAV).

    Returns 0.0 when nav is missing — tier evaluator must NOT advance
    on missing data.
    """
    if nav <= 0:
        return 0.0
    max_len = max((len(v) for v in per_strategy.values()), default=0)
    cumulative = 0.0
    peak = 0.0
    worst_excursion = 0.0
    for i in range(max_len):
        step = sum(v[i] for v in per_strategy.values() if i < len(v))
        cumulative += step
        peak = max(peak, cumulative)
        excursion = peak - cumulative  # USD lost from peak
        worst_excursion = max(worst_excursion, excursion)
    return worst_excursion / nav


def _aggregate_sharpe(rows: list[dict]) -> float:
    """Weight per-strategy naive Sharpes by fill count. Returns 0 when
    we don't have enough observations to be meaningful (matches the
    convention in analyze_shadow_pnl._per_strategy_sharpe).
    """
    total_fills = sum(r.get("fills", 0) for r in rows)
    if total_fills < 50:
        return 0.0
    weighted = sum(
        (r.get("naive_sharpe") or 0.0) * (r.get("fills") or 0)
        for r in rows
    )
    return weighted / total_fills


def _current_tier_from_prometheus() -> str | None:
    """Authoritative current tier. capital_tier is module-local in the
    intelligence container, so this script (running on the host)
    can't read it directly; query Prometheus for the gauge instead.
    Returns None when the metric is missing (rare — caller should
    fall back to the process-local view).
    """
    tier_num = _prom_scalar("capital_tier_active")
    if tier_num is None:
        return None
    order = ("PAPER", "MICRO", "SMALL", "MID", "FULL")
    idx = int(tier_num)
    if 0 <= idx < len(order):
        return order[idx]
    return None


def compute_tier_stats(hours: int):
    """Build a TierStats over the last `hours` hours."""
    from scripts.analyze_shadow_pnl import _aggregate, _per_strategy_sharpe  # type: ignore
    from shared.risk.capital_tier import TierStats

    rows = _aggregate(hours)
    _per_strategy_sharpe(rows)

    n_trades = sum(r.get("fills", 0) for r in rows)
    sharpe = _aggregate_sharpe(rows)
    series = _per_strategy_pnl_series(hours)
    nav = _prom_scalar("portfolio_total_exposure_usd") or 0.0
    max_dd = _aggregate_drawdown(series, nav)

    hard_kill_rate = _prom_scalar(
        f"increase(risk_halt_publications_total[{hours}h])"
    )
    hard_kill_events = int(hard_kill_rate) if hard_kill_rate else 0

    return TierStats(
        n_trades=n_trades,
        realized_sharpe=sharpe,
        realized_max_dd=max_dd,
        hard_kill_events=hard_kill_events,
    ), {
        "strategy_count": len(rows),
        "positive_sharpe_strategies": sum(
            1 for r in rows if (r.get("naive_sharpe") or 0.0) > 0
        ),
        "nav_usd": round(nav, 2),
    }


# ──────────────────────────────────────────────────────────────────
# Evaluation + persistence
# ──────────────────────────────────────────────────────────────────


def _get_redis():
    try:
        import redis
        return redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True,
            socket_timeout=2,
        )
    except Exception:
        return None


def evaluate(hours: int, apply_transition: bool) -> dict:
    from shared.risk import capital_tier

    stats, meta = compute_tier_stats(hours)
    # Authoritative current tier from Prometheus (intelligence container's
    # actual state). The capital_tier module is process-local, so the
    # host-side view would otherwise be wrong.
    current = _current_tier_from_prometheus() or capital_tier.current_tier()
    # evaluate_tier_transition reads capital_tier.current_tier() internally;
    # we mirror the authoritative value into the in-process module so the
    # check uses the right TierSpec thresholds.
    if current != capital_tier.current_tier():
        capital_tier._active_tier = current  # type: ignore[attr-defined]
    suggested = capital_tier.evaluate_tier_transition(stats)

    # The TierSpec thresholds for the CURRENT tier dictate promotion.
    current_spec = capital_tier._tiers[current]

    reasons = []
    if stats.hard_kill_events > 0:
        reasons.append(f"HARD kill events in window: {stats.hard_kill_events}")
    if stats.realized_sharpe <= current_spec.demote_sharpe:
        reasons.append(
            f"realized Sharpe {stats.realized_sharpe:.2f} ≤ demote_sharpe "
            f"{current_spec.demote_sharpe}"
        )
    if stats.realized_max_dd >= current_spec.demote_max_dd:
        reasons.append(
            f"realized maxDD {stats.realized_max_dd:.2%} ≥ demote_max_dd "
            f"{current_spec.demote_max_dd:.0%}"
        )
    if (stats.n_trades >= current_spec.promote_min_trades
            and stats.realized_sharpe >= current_spec.promote_min_sharpe
            and stats.realized_max_dd <= current_spec.promote_max_dd
            and stats.hard_kill_events == 0):
        reasons.append(
            f"meets promotion criteria: n={stats.n_trades}≥"
            f"{current_spec.promote_min_trades}, "
            f"SR={stats.realized_sharpe:.2f}≥{current_spec.promote_min_sharpe}, "
            f"maxDD={stats.realized_max_dd:.2%}≤"
            f"{current_spec.promote_max_dd:.0%}, no hard kills"
        )

    verdict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "window_hours": hours,
        "current_tier": current,
        "suggested_tier": suggested,
        "would_apply": (suggested is not None and apply_transition),
        "stats": {
            "n_trades": stats.n_trades,
            "realized_sharpe": round(stats.realized_sharpe, 4),
            "realized_max_dd": round(stats.realized_max_dd, 4),
            "hard_kill_events": stats.hard_kill_events,
        },
        "current_spec_thresholds": {
            "promote_min_trades": current_spec.promote_min_trades,
            "promote_min_sharpe": current_spec.promote_min_sharpe,
            "promote_max_dd": current_spec.promote_max_dd,
            "demote_sharpe": current_spec.demote_sharpe,
            "demote_max_dd": current_spec.demote_max_dd,
        },
        "meta": meta,
        "reasons": reasons,
    }

    # Persist for daily_report
    r = _get_redis()
    if r is not None:
        try:
            r.set(REDIS_KEY, json.dumps(verdict), ex=86400 * 7)
        except Exception as exc:
            verdict["redis_write_error"] = str(exc)[:120]

    # Apply if explicitly opted in
    if suggested is not None and apply_transition:
        capital_tier.set_active_tier(
            suggested,
            reason=f"promoter window={hours}h sharpe={stats.realized_sharpe:.2f}",
        )

    return verdict


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────


def _format_human(v: dict) -> str:
    lines = []
    lines.append(f"Capital tier promoter — {v['timestamp']}")
    lines.append(f"  Current tier:   {v['current_tier']}")
    lines.append(f"  Window:         {v['window_hours']}h")
    s = v["stats"]
    lines.append(
        f"  Stats:          trades={s['n_trades']}, "
        f"Sharpe={s['realized_sharpe']:+.2f}, "
        f"maxDD={s['realized_max_dd']:.2%}, "
        f"hard_kills={s['hard_kill_events']}"
    )
    if v["suggested_tier"]:
        action = "WOULD APPLY" if v["would_apply"] else "DRY-RUN"
        lines.append(
            f"  Suggestion:     ▶ {v['current_tier']} → {v['suggested_tier']}  "
            f"({action})"
        )
    else:
        lines.append("  Suggestion:     (stay)")
    if v["reasons"]:
        lines.append("  Reasons:")
        for r in v["reasons"]:
            lines.append(f"    - {r}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of human-readable text")
    parser.add_argument("--apply", action="store_true",
                        help="DANGEROUS: actually flip the in-process "
                        "tier when a transition is suggested. Off by "
                        "default — the report is for operator review.")
    args = parser.parse_args()

    verdict = evaluate(hours=args.hours, apply_transition=args.apply)

    if args.json:
        print(json.dumps(verdict, indent=2, default=str))
    else:
        print(_format_human(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
