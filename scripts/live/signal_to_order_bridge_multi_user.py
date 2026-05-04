#!/usr/bin/env python3
"""Multi-user signal → order bridge.

Sister script to signal_to_order_bridge.py. The single-user bridge takes
ONE Binance API key (from CLI / env) and fires the latest signal at one
account. This one fans the SAME signal out to every user who has:

  - automation_enabled = True   (PATCH /auth/me/automation)
  - a stored Binance credential (POST /settings/credentials)
  - kill switch not in HARD/PANIC for their per-user state

Per-user isolation:
  - Separate BinanceFuturesConnector instance (their own keys)
  - Separate PositionTracker (their own audit log under data/logs/reconciliation/user_{id}/)
  - Separate execution log under data/logs/execution/user_{id}/
  - One user's failure (bad key, network error, exchange reject) is logged
    and skipped; other users continue.

Sequential by design — Binance's 1200/min weight limit is shared across
all our calls, so parallel fan-out can DoS us off the API. With a few
dozen users the sequential cost is sub-second per user.

Modes (mutually exclusive):
  --dry-run   (default) — fetch users + creds but don't connect to exchange
  --testnet              — Binance Futures testnet (uses each user's testnet keys)
  --live                 — Binance Futures mainnet (REAL MONEY)

Usage:
    # cron entry replacing the single-user bridge:
    INTERNAL_ADMIN_SECRET=... \\
    python3 scripts/live/signal_to_order_bridge_multi_user.py --testnet

Required env:
    INTERNAL_ADMIN_SECRET    — used to call auth-service / credential-store
    AUTH_SERVICE_BASE_URL    — default http://auth-service:8000
    CREDENTIAL_STORE_BASE_URL — default http://credential-store:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from shared.internal_admin import build_internal_admin_headers  # noqa: E402
from shared.risk.kill_switch import is_kill_switch_active  # noqa: E402

# Reuse the building blocks from the single-user bridge.
from scripts.live.signal_to_order_bridge import (  # noqa: E402
    EXEC_LOG_DIR,
    build_targets,
    find_latest_signal_file,
    load_signals,
)

ACTOR = "signal-bridge-multi-user"
AUTH_BASE = os.getenv("AUTH_SERVICE_BASE_URL", "http://auth-service:8000")
CRED_BASE = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://credential-store:8000")
HTTP_TIMEOUT = 10.0


def _internal_get(base: str, path: str, secret: str) -> Any:
    url = base.rstrip("/") + path
    headers = build_internal_admin_headers(secret, actor_user_id=ACTOR, path=path)
    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        r = c.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


def fetch_active_users(secret: str) -> list[dict]:
    """Users with automation_enabled=True. Empty list on failure (logged)."""
    try:
        users = _internal_get(AUTH_BASE, "/admin/users", secret)
    except Exception as e:
        print(f"  [error] fetch_active_users: {e}")
        return []
    return [u for u in users if u.get("automation_enabled")]


def fetch_user_credential(user_id: str, exchange: str, secret: str) -> dict | None:
    """Returns {api_key, api_secret, sandbox} or None if not registered."""
    try:
        return _internal_get(
            CRED_BASE, f"/credentials/{user_id}/{exchange}/reveal", secret
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        print(f"  [error] fetch_user_credential({user_id}): {e}")
        return None
    except Exception as e:
        print(f"  [error] fetch_user_credential({user_id}): {e}")
        return None


def execute_for_user(
    *, user: dict, signals: list[dict], mode: str, cred: dict,
    max_pos_per_symbol: float, max_gross: float, max_dd: float,
) -> dict:
    """Reconcile + execute for one user. Returns summary dict."""
    user_id = user["user_id"]
    from shared.execution.binance_futures import BinanceFuturesConnector
    from shared.execution.position_tracker import PositionTracker
    from shared.execution.order_executor import OrderExecutor
    from shared.execution.risk_limits import RiskLimits

    user_log_root = REPO_ROOT / "data" / "logs" / "execution" / f"user_{user_id}"
    user_recon_dir = REPO_ROOT / "data" / "logs" / "reconciliation" / f"user_{user_id}"
    user_log_root.mkdir(parents=True, exist_ok=True)

    testnet = mode == "testnet" or bool(cred.get("sandbox"))
    connector = BinanceFuturesConnector(
        api_key=cred["api_key"], api_secret=cred["api_secret"], testnet=testnet,
    )

    equity = connector.get_account_equity()
    if equity <= 0:
        return {"user_id": user_id, "status": "skip", "reason": "zero_equity"}

    symbols = [s["symbol"] for s in signals if "symbol" in s]
    prices = connector.get_mark_prices(symbols)
    targets, log = build_targets(signals, equity, prices)

    tracker = PositionTracker(connector, min_trade_notional=10, audit_log_dir=user_recon_dir)
    recon = tracker.reconcile(targets, prices)

    limits = RiskLimits(
        max_position_per_symbol=max_pos_per_symbol,
        max_total_exposure=max_gross,
        max_drawdown_halt=max_dd,
        min_order_size_usd=10.0,
    )
    executor = OrderExecutor(
        connector, risk_limits=limits, dry_run=False, log_dir=str(user_log_root),
    )
    current_notional = {s: q * prices.get(s, 0) for s, q in recon.actual_positions.items()}
    result = executor.execute(
        recon.orders_needed, equity=equity,
        current_positions=current_notional, prices=prices,
    )
    return {
        "user_id": user_id,
        "status": "ok",
        "equity": equity,
        "n_targets": len(targets),
        "n_orders": len(recon.orders_needed),
        "filled": result.orders_filled,
        "failed": result.orders_failed,
        "notional": result.total_notional,
    }


def fan_out(args: argparse.Namespace, signals: list[dict], secret: str) -> dict:
    users = fetch_active_users(secret)
    print(f"  active users: {len(users)}")
    if not users:
        return {"users_processed": 0, "users": []}

    summaries: list[dict] = []
    for u in users:
        user_id = u["user_id"]
        # Per-user kill switch: HARD or PANIC → skip (no fan-out for this user)
        ks_active, ks_level = is_kill_switch_active(user_id)
        if ks_active:
            summaries.append({"user_id": user_id, "status": "skip", "reason": f"kill_switch_{ks_level}"})
            print(f"    {user_id} → SKIP ({ks_level})")
            continue

        cred = fetch_user_credential(user_id, "binance", secret)
        if cred is None:
            summaries.append({"user_id": user_id, "status": "skip", "reason": "no_binance_credential"})
            print(f"    {user_id} → SKIP (no binance credential)")
            continue

        if args.dry_run:
            summaries.append({"user_id": user_id, "status": "dry-run", "has_credential": True})
            print(f"    {user_id} → DRY-RUN ok (would route signals to their account)")
            continue

        try:
            summary = execute_for_user(
                user=u, signals=signals, mode=("testnet" if args.testnet else "live"),
                cred=cred,
                max_pos_per_symbol=args.max_position_per_symbol,
                max_gross=args.max_gross_exposure,
                max_dd=args.max_drawdown_halt,
            )
            summaries.append(summary)
            print(f"    {user_id} → {summary['status']} (filled={summary.get('filled', 0)})")
        except Exception as e:
            summaries.append({"user_id": user_id, "status": "error", "reason": str(e)})
            print(f"    {user_id} → ERROR ({e})")

    return {"users_processed": len(summaries), "users": summaries}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--testnet", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--confirm-live", action="store_true",
                    help="required with --live to acknowledge real-money execution")
    ap.add_argument("--max-signal-age-hours", type=float, default=2.0)
    ap.add_argument("--max-position-per-symbol", type=float, default=0.25)
    ap.add_argument("--max-gross-exposure", type=float, default=1.5)
    ap.add_argument("--max-drawdown-halt", type=float, default=0.15)
    args = ap.parse_args()

    if args.live and not args.confirm_live:
        print("ERROR: --live requires --confirm-live")
        return 2

    # Live preflight (refuses if global kill switch is in PANIC, etc.)
    if args.live or args.testnet:
        from shared.execution.mode import assert_live_safe, ExecutionMode
        os.environ["EXECUTION_MODE"] = "live" if args.live else "testnet"
        try:
            assert_live_safe(confirm_flag=args.confirm_live or args.testnet)
        except RuntimeError as e:
            # testnet doesn't gate on LIVE_TRADING_ENABLED — translate
            if args.testnet:
                pass
            else:
                print(f"ERROR: live preflight failed: {e}")
                return 2

    secret = os.getenv("INTERNAL_ADMIN_SECRET", "")
    if not secret:
        print("ERROR: INTERNAL_ADMIN_SECRET env required for multi-user fan-out")
        return 2

    sig_path = find_latest_signal_file(args.max_signal_age_hours)
    if not sig_path:
        print(f"ERROR: no signals newer than {args.max_signal_age_hours}h")
        return 1
    signals = load_signals(sig_path)
    print(f"\n[multi-user bridge] signals={len(signals)} from {sig_path.name}")
    print(f"  mode={'live' if args.live else 'testnet' if args.testnet else 'dry-run'}")

    summary = fan_out(args, signals, secret)

    # Persist run summary for ops + /soak page consumption later.
    out_dir = REPO_ROOT / "data" / "logs" / "bridge_multi_user"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if args.live else "testnet" if args.testnet else "dry-run",
        "signal_file": sig_path.name,
        **summary,
    }, indent=2))
    print(f"\n  summary written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
