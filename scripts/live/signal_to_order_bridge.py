#!/usr/bin/env python3
"""Signal → Order bridge.

Reads the latest signal JSON produced by `generate_signals.py`, converts
each symbol's `target_position` fraction into an exchange quantity, and
reconciles against the exchange's current positions via PositionTracker
+ OrderExecutor.

Honours the live guard from generate_signals.py:
  - signal.parked == True  → target quantity = 0 (liquidate)
  - signal missing/error   → skip (don't touch existing position)

Modes (mutually exclusive):
  --dry-run  (default)  — no exchange calls, log plan only
  --testnet             — Binance Futures testnet (real API, fake money)
  --live                — Binance Futures mainnet (REAL MONEY — requires 2-week testnet soak + explicit confirm)

Safety rails:
  - Pre-trade RiskLimits (DD halt, per-symbol cap, turnover cap)
  - Skips orders < min_order_size_usd (10 USD)
  - Requires signal file newer than --max-signal-age-hours (default 2h)
  - Records every decision + exchange response to data/logs/execution/

This bridge does NOT regenerate signals. It is a thin consumer of the
signals written by `generate_signals.py`. This separation makes it easy
to compare paper vs testnet fill rates later: both consume the same JSON.

Usage:
    python3 scripts/live/signal_to_order_bridge.py --dry-run
    python3 scripts/live/signal_to_order_bridge.py --testnet --api-key ... --api-secret ...
    python3 scripts/live/signal_to_order_bridge.py --live --confirm-live --api-key ... --api-secret ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.engine.config import load_config  # noqa: E402
from shared.execution.mode import is_live, get_ramp_factor  # noqa: E402

SIGNALS_DIR = REPO_ROOT / "data" / "signals"
# Separate log directories per execution route — NEVER let them mix.
# /execution       → testnet + live (real & testnet Binance)
# /virtual_execution → virtual in-memory simulator
# /dry_run_execution → no-op plan-only mode
EXEC_LOG_DIR = REPO_ROOT / "data" / "logs" / "execution"
VIRTUAL_LOG_DIR = REPO_ROOT / "data" / "logs" / "virtual_execution"
DRY_RUN_LOG_DIR = REPO_ROOT / "data" / "logs" / "dry_run_execution"
CONFIG_PATH = REPO_ROOT / "config" / "v4_production.json"

# Virtual-mode defaults (must stay under data/virtual/ — enforced by the
# VirtualFuturesConnector's own tripwire.)
VIRTUAL_STATE_FILE = REPO_ROOT / "data" / "virtual" / "state.json"
VIRTUAL_HISTORY_FILE = REPO_ROOT / "data" / "virtual" / "history.jsonl"

UTC = timezone.utc


def find_latest_signal_file(max_age_hours: float) -> Path | None:
    """Return the newest signals_*.json, or None if too stale."""
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(SIGNALS_DIR.glob("signals_*.json"), reverse=True)
    if not files:
        return None
    newest = files[0]
    age = datetime.now(UTC).timestamp() - newest.stat().st_mtime
    if age > max_age_hours * 3600:
        return None
    return newest


def load_signals(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def build_targets(
    signals: list[dict],
    equity: float,
    prices: dict[str, float],
) -> tuple[dict[str, float], dict[str, dict]]:
    """Convert signals → (target_quantities, decision_log).

    target_quantities: symbol -> signed base-asset quantity
    decision_log:      symbol -> {parked, target_pos, price, quantity, reason, ramp_factor}

    Live ramp: in live mode the staged ramp factor (config.ramp.factor,
    managed by scripts/live/ramp_controller.py) multiplies the notional.
    Non-live modes use 1.0 — paper/virtual/testnet always run at full
    intended size so they remain comparable to backtest. The applied
    factor is recorded in decision_log for audit.
    """
    ramp_factor = get_ramp_factor() if is_live() else 1.0
    targets: dict[str, float] = {}
    log: dict[str, dict] = {}
    for sig in signals:
        sym = sig.get("symbol")
        if not sym:
            continue
        if "error" in sig:
            log[sym] = {"skip": "signal error", "detail": sig["error"]}
            continue

        target_pos = float(sig.get("target_position", 0.0))
        price = float(prices.get(sym, sig.get("price", 0.0)))
        parked = bool(sig.get("parked", False))
        guard = sig.get("live_guard", "?")

        if parked:
            targets[sym] = 0.0
            log[sym] = {
                "parked": True,
                "guard": guard,
                "target_pos_fraction": target_pos,
                "price": price,
                "quantity": 0.0,
                "reason": sig.get("parked_reason") or guard,
                "ramp_factor": ramp_factor,
            }
            continue

        if price <= 0:
            log[sym] = {"skip": "no price"}
            continue

        notional = equity * target_pos * ramp_factor
        quantity = round(notional / price, 6)
        targets[sym] = quantity
        log[sym] = {
            "parked": False,
            "guard": guard,
            "target_pos_fraction": target_pos,
            "price": price,
            "notional": notional,
            "quantity": quantity,
            "ramp_factor": ramp_factor,
        }
    return targets, log


def _log_dir_for_mode(mode: str) -> Path:
    """Route each mode to its own log directory. Prevents cross-contamination
    between dry-run, virtual, testnet, and live history."""
    if mode in ("testnet", "live"):
        return EXEC_LOG_DIR
    if mode == "virtual":
        return VIRTUAL_LOG_DIR
    return DRY_RUN_LOG_DIR


def write_execution_log(payload: dict, mode: str = "dry-run"):
    log_dir = _log_dir_for_mode(mode)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    prefix = {"dry-run": "dry", "virtual": "virt", "testnet": "tnet", "live": "live"}.get(mode, "dry")
    path = log_dir / f"bridge_{prefix}_{ts}.json"
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def run_dry_run(signals, equity_override: float) -> dict:
    equity = equity_override
    # Use signal.price as dry-run price (no exchange call)
    prices = {s["symbol"]: float(s["price"]) for s in signals if "price" in s}
    targets, log = build_targets(signals, equity, prices)

    print(f"\n[DRY-RUN] equity=${equity:,.0f}")
    print(f"  signals={len(signals)}  targets={len(targets)}")
    active = sum(1 for v in log.values() if not v.get("parked") and "skip" not in v)
    parked = sum(1 for v in log.values() if v.get("parked"))
    skipped = sum(1 for v in log.values() if "skip" in v)
    print(f"  active={active}  parked={parked}  skipped={skipped}")

    print(f"\n  Planned positions (hypothetical fill at signal price):")
    gross = 0.0
    for sym in sorted(log.keys()):
        entry = log[sym]
        if entry.get("parked"):
            print(f"    {sym:10s}  🔒 PARK (guard={entry.get('guard')}, flat)")
            continue
        if "skip" in entry:
            print(f"    {sym:10s}  ⏭  skip: {entry['skip']}")
            continue
        tp = entry["target_pos_fraction"]
        qty = entry["quantity"]
        notional = entry["notional"]
        gross += abs(notional)
        print(f"    {sym:10s}  pos={tp:+.4f}  qty={qty:+.6f}  notional=${notional:+,.2f}")
    print(f"\n  Gross exposure: ${gross:,.2f}  ({gross / equity:.1%} of equity)")
    print("  [DRY-RUN] No orders sent.")

    return {"mode": "dry-run", "equity": equity, "targets": targets, "log": log}


def run_virtual(signals,
                initial_equity: float,
                max_pos_per_symbol: float, max_gross: float, max_dd: float,
                state_file: Path | None = None,
                history_file: Path | None = None,
                realism_enabled: bool = True) -> dict:
    """Execute against the in-memory VirtualFuturesConnector.

    No API keys. No external orders. State persists under data/virtual/
    (enforced by the connector's tripwire).

    Uses the same PositionTracker + OrderExecutor code path as run_live
    so the virtual run exercises production execution logic — the only
    difference is the connector implementation.
    """
    from shared.execution.virtual_futures import VirtualFuturesConnector, RealismConfig
    from shared.execution.position_tracker import PositionTracker
    from shared.execution.order_executor import OrderExecutor
    from shared.execution.risk_limits import RiskLimits

    # Default realism = on so virtual sim charges market-impact slippage
    # (linear up to 10 bps). Without this, virtual results look better than
    # what live taker-fills will produce → false confidence at ramp-up time.
    realism = RealismConfig(slippage_enabled=True) if realism_enabled else None
    connector = VirtualFuturesConnector(
        initial_equity=initial_equity,
        state_file=state_file,
        history_file=history_file,
        reset=False,  # never auto-reset; use scripts/virtual/reset.py explicitly
        realism=realism,
    )

    equity = connector.get_account_equity()
    print(f"\n[VIRTUAL] in-memory futures sim — equity=${equity:,.2f}")

    symbols = [s["symbol"] for s in signals if "symbol" in s]
    prices = connector.get_mark_prices(symbols)
    targets, log = build_targets(signals, equity, prices)

    active = sum(1 for v in log.values() if not v.get("parked") and "skip" not in v)
    parked = sum(1 for v in log.values() if v.get("parked"))
    print(f"  signals={len(signals)}  active={active}  parked={parked}")

    tracker = PositionTracker(connector, min_trade_notional=5)
    recon = tracker.reconcile(targets, prices)
    print(f"  orders_needed={len(recon.orders_needed)}  skipped={len(recon.skipped)}")
    for o in recon.orders_needed:
        print(f"    {o.side} {o.symbol} qty={o.quantity:+.6f}")

    # In the virtual sim, any single rebalance order can be up to the per-symbol
    # position cap — the engine rebalances in one shot, not TWAP-style.
    limits = RiskLimits(
        max_position_per_symbol=max_pos_per_symbol,
        max_total_exposure=max_gross,
        max_drawdown_halt=max_dd,
        max_single_order_notional=max_pos_per_symbol,
        min_order_size_usd=5.0,
    )
    executor = OrderExecutor(
        connector, risk_limits=limits, dry_run=False,
        log_dir=str(VIRTUAL_LOG_DIR),
    )
    current_notional = {s: q * prices.get(s, 0) for s, q in recon.actual_positions.items()}
    result = executor.execute(
        recon.orders_needed, equity=equity,
        current_positions=current_notional, prices=prices,
    )
    print(f"\n  virtual execution: filled={result.orders_filled} "
          f"failed={result.orders_failed} notional=${result.total_notional:,.2f}")

    snap_after = connector.snapshot()
    return {
        "mode": "virtual",
        "equity_before": equity,
        "equity_after": snap_after["equity"],
        "balance_after": snap_after["balance"],
        "realized_pnl": snap_after["realized_pnl"],
        "unrealized_pnl": snap_after["unrealized_pnl"],
        "total_fees": snap_after["total_fees"],
        "positions_after": snap_after["positions"],
        "n_orders": snap_after["n_orders"],
        "n_fills": snap_after["n_fills"],
        "n_rejected": snap_after["n_rejected"],
        "targets": targets,
        "orders_needed": [{"symbol": o.symbol, "side": o.side, "quantity": o.quantity} for o in recon.orders_needed],
        "filled": result.orders_filled,
        "failed": result.orders_failed,
        "log": log,
    }


def run_live(signals, mode: str, api_key: str, api_secret: str,
             max_pos_per_symbol: float, max_gross: float, max_dd: float) -> dict:
    """mode ∈ {'testnet', 'live'}."""
    from shared.execution.binance_futures import BinanceFuturesConnector
    from shared.execution.position_tracker import PositionTracker
    from shared.execution.order_executor import OrderExecutor
    from shared.execution.risk_limits import RiskLimits

    testnet = mode == "testnet"
    connector = BinanceFuturesConnector(api_key=api_key, api_secret=api_secret, testnet=testnet)

    # Mainnet only: refuse keys with withdrawal/transfer permission. This is
    # a self-custody guard — even if the engine is compromised it cannot
    # drain funds, only reshuffle futures positions.
    if mode == "live":
        try:
            perms = connector.validate_permissions()
            print(f"  api-key permissions OK: {perms}")
        except PermissionError as exc:
            print(f"\n  ✗ API KEY PERMISSION CHECK FAILED:\n    {exc}\n")
            return {"mode": mode, "error": "api_permission_unsafe", "detail": str(exc)}

    equity = connector.get_account_equity()
    print(f"\n[{mode.upper()}] Binance Futures — account equity=${equity:,.2f}")
    if equity <= 0:
        print("  ERROR: zero equity — fund the account before running.")
        return {"mode": mode, "error": "zero_equity"}

    symbols = [s["symbol"] for s in signals if "symbol" in s]
    prices = connector.get_mark_prices(symbols)
    targets, log = build_targets(signals, equity, prices)

    active = sum(1 for v in log.values() if not v.get("parked") and "skip" not in v)
    parked = sum(1 for v in log.values() if v.get("parked"))
    print(f"  signals={len(signals)}  active={active}  parked={parked}")

    tracker = PositionTracker(connector, min_trade_notional=10)
    recon = tracker.reconcile(targets, prices)
    print(f"  orders_needed={len(recon.orders_needed)}  skipped={len(recon.skipped)}")
    for o in recon.orders_needed:
        print(f"    {o.side} {o.symbol} qty={o.quantity:+.6f}")

    limits = RiskLimits(
        max_position_per_symbol=max_pos_per_symbol,
        max_total_exposure=max_gross,
        max_drawdown_halt=max_dd,
        min_order_size_usd=10.0,
    )
    executor = OrderExecutor(
        connector, risk_limits=limits, dry_run=False,
        log_dir=str(EXEC_LOG_DIR),
    )
    current_notional = {s: q * prices.get(s, 0) for s, q in recon.actual_positions.items()}
    result = executor.execute(
        recon.orders_needed, equity=equity,
        current_positions=current_notional, prices=prices,
    )
    print(f"\n  execution: filled={result.orders_filled} failed={result.orders_failed} "
          f"notional=${result.total_notional:,.2f}")
    return {
        "mode": mode,
        "equity": equity,
        "targets": targets,
        "orders_needed": [{"symbol": o.symbol, "side": o.side, "quantity": o.quantity} for o in recon.orders_needed],
        "filled": result.orders_filled,
        "failed": result.orders_failed,
        "total_notional": result.total_notional,
        "log": log,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode_group = ap.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", default=True)
    mode_group.add_argument("--virtual", action="store_true",
                            help="execute against in-memory VirtualFuturesConnector "
                                 "(state under data/virtual/, no API keys needed)")
    mode_group.add_argument("--testnet", action="store_true")
    mode_group.add_argument("--live", action="store_true")

    ap.add_argument("--confirm-live", action="store_true",
                    help="required with --live to acknowledge real-money execution")
    ap.add_argument("--skip-readiness", action="store_true",
                    help="(--live only) bypass live_readiness scorecard. Only use after manual scorecard review.")
    ap.add_argument("--api-key", default=os.getenv("BINANCE_API_KEY", ""))
    ap.add_argument("--api-secret", default=os.getenv("BINANCE_API_SECRET", ""))
    ap.add_argument("--equity-override", type=float, default=10_000.0,
                    help="dry-run notional equity (default $10k); for --virtual, only used on first init")
    ap.add_argument("--max-signal-age-hours", type=float, default=2.0,
                    help="refuse to execute signals older than this (default 2h)")
    ap.add_argument("--max-position-per-symbol", type=float, default=0.25)
    ap.add_argument("--max-gross-exposure", type=float, default=1.5)
    ap.add_argument("--max-drawdown-halt", type=float, default=0.15)
    ap.add_argument("--virtual-state-file", default=None,
                    help="override virtual state path (must contain '/virtual/')")
    ap.add_argument("--virtual-history-file", default=None,
                    help="override virtual history path (must contain '/virtual/')")
    ap.add_argument("--no-realism", action="store_true",
                    help="(virtual mode only) disable slippage/impact modeling. "
                         "Defaults OFF — leave realism on so virtual results match live cost regime.")
    args = ap.parse_args()

    # Resolve mode (argparse sets dry-run default True even when another is chosen,
    # so recompute from the explicit flags).
    live = args.live
    testnet = args.testnet and not live
    virtual = args.virtual and not (live or testnet)
    dry = not (live or testnet or virtual)
    mode = ("live" if live
            else "testnet" if testnet
            else "virtual" if virtual
            else "dry-run")

    # Isolation guards
    if live and not args.confirm_live:
        print("ERROR: --live requires --confirm-live to acknowledge real-money risk.")
        return 2
    if (live or testnet) and not (args.api_key and args.api_secret):
        print("ERROR: --testnet/--live requires --api-key and --api-secret "
              "(or env BINANCE_API_KEY/SECRET).")
        return 2

    # Readiness gate — refuse live unless live_readiness.py says GO. This
    # catches the case where soak ran with funding/slippage off (sim was
    # too optimistic) or alpha health is failing vs OOS expectations.
    if live and not args.skip_readiness:
        from scripts.live.live_readiness import (
            score_realism, score_soak, score_alpha_health,
            score_risk_infra, score_recovery, composite_verdict,
        )
        dims = [
            score_realism(), score_soak(), score_alpha_health(),
            score_risk_infra(), score_recovery(),
        ]
        composite, verdict = composite_verdict(dims)
        if verdict != "GO":
            print(f"\n  ✗ READINESS GATE BLOCKED --live  (composite={composite}/100, verdict={verdict})")
            print("    Run `python3 scripts/live/live_readiness.py` for the per-dimension breakdown.")
            print("    To override (NOT recommended), pass --skip-readiness AFTER manual review.\n")
            return 2
        print(f"  readiness gate passed: {composite}/100 GO")
    # Virtual mode must not use API creds — refuse if caller passes them
    # (to catch copy-paste slip between commands).
    if virtual and (args.api_key or args.api_secret):
        print("ERROR: --virtual does not use API credentials. Unset --api-key/--api-secret "
              "(or BINANCE_API_KEY/SECRET env) to avoid confusion with testnet/live.")
        return 2

    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M UTC}] signal→order bridge  mode={mode}")

    sig_path = find_latest_signal_file(args.max_signal_age_hours)
    if sig_path is None:
        print(f"  ERROR: no signal file found in last {args.max_signal_age_hours}h. "
              f"Run generate_signals.py first.")
        return 3
    print(f"  signal file: {sig_path.name}")

    signals = load_signals(sig_path)
    cfg = load_config(CONFIG_PATH) if CONFIG_PATH.exists() else None
    if cfg is not None:
        tracked = set(cfg.symbols) | set(cfg.symbols_parked.keys())
        signals = [s for s in signals if s.get("symbol") in tracked]

    if dry:
        payload = run_dry_run(signals, args.equity_override)
    elif virtual:
        # Default paths go under data/virtual/; overrides still must contain '/virtual/'
        # (the connector itself enforces this via tripwire).
        state_file = Path(args.virtual_state_file) if args.virtual_state_file else VIRTUAL_STATE_FILE
        history_file = Path(args.virtual_history_file) if args.virtual_history_file else VIRTUAL_HISTORY_FILE
        payload = run_virtual(
            signals, args.equity_override,
            args.max_position_per_symbol, args.max_gross_exposure, args.max_drawdown_halt,
            state_file=state_file, history_file=history_file,
            realism_enabled=not args.no_realism,
        )
    else:
        payload = run_live(
            signals, mode, args.api_key, args.api_secret,
            args.max_position_per_symbol, args.max_gross_exposure, args.max_drawdown_halt,
        )

    payload["signal_file"] = str(sig_path)
    payload["timestamp"] = datetime.now(UTC).isoformat()
    log_path = write_execution_log(payload, mode=mode)
    print(f"  log → {log_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
