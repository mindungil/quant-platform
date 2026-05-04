#!/usr/bin/env python3
"""v4.3 Live Signal Generator (config-driven + live guard).

Fetches the latest OHLCV from Binance, runs the per-symbol alpha set
defined in `config/v4_production.json`, and outputs:
  1) Current recommended position per symbol (-1 to +1)
  2) Position change from previous bar (trade signal)
  3) Recent 30-day and 180-day rolling Sharpe
  4) Per-alpha attribution
  5) Live-guard verdict: ACTIVE, WARN (half-size), PARKED (flat), CONFIG_PARKED

Changes vs v4.1 live script (2026-04-24):
  - Reads config/v4_production.json as single source of truth
  - Per-symbol asset_overrides alpha selection (v4.3 had 3 different
    alphas per asset: momentum_ensemble + range_reversion + vol_breakout)
  - Respects symbols_parked (SOLUSDT/XRPUSDT/DOGEUSDT → flat by default)
  - 6M rolling Sharpe auto-park: if live SR < park_threshold (-0.5),
    force target_position=0 and tag `live_guard=PARKED`. Half-size on
    WARN (SR < 0). Prevents alpha-decay-driven bleeding.
  - Signal JSON carries `parked`, `live_guard`, `live_6m_sharpe` fields
    so paper_portfolio and downstream consumers can gate execution.

Usage:
    python3 scripts/live/generate_signals.py
    python3 scripts/live/generate_signals.py --symbols BTCUSDT,ETHUSDT
    python3 scripts/live/generate_signals.py --config config/v4_production.json
    python3 scripts/live/generate_signals.py --no-live-guard  # bypass for debugging
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from shared.alpha.base import AlphaConfig  # noqa: E402
from shared.alpha.range_reversion import RangeReversionAlpha  # noqa: E402
from shared.alpha.registry import get_alpha  # noqa: E402
from shared.backtest.metrics import sharpe_ratio  # noqa: E402
from shared.engine.config import EngineConfig, alphas_for_symbol, is_symbol_parked, load_config  # noqa: E402
from shared.portfolio import EnsembleAllocator, EnsembleConfig  # noqa: E402
from shared.regime import VolTrendRegime  # noqa: E402

from scripts.data.fetch_binance_klines import fetch_full_history  # noqa: E402

# AFFINITY: regime → alpha weights. Built in-code (kept from v4.1).
# kalman_trend entry removed 2026-05-04 (demoted from production set 2026-04-30).
AFFINITY = {
    "momentum_ensemble": {"TREND_UP": 1.4, "TREND_DOWN": 1.4, "RANGE": 0.5, "CRISIS": 0.4},
    "trend_breakout":    {"TREND_UP": 1.5, "TREND_DOWN": 1.5, "RANGE": 0.4, "CRISIS": 0.6},
    "vol_breakout":      {"TREND_UP": 1.2, "TREND_DOWN": 1.2, "RANGE": 0.5, "CRISIS": 1.0},
    "range_reversion":   {"TREND_UP": 0.5, "TREND_DOWN": 0.5, "RANGE": 1.3, "CRISIS": 0.7},
    "funding_carry":     {"TREND_UP": 0.8, "TREND_DOWN": 0.8, "RANGE": 1.0, "CRISIS": 1.2},
}

UTC = timezone.utc


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "1h":
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule, label="right", closed="right").agg(agg).dropna(subset=["close"])


def fetch_recent(symbol: str, lookback_days: int = 220, timeframe: str = "1h") -> pd.DataFrame:
    """Fetch recent OHLCV. Lookback defaults to 220d so 180d (6M) Sharpe is stable."""
    end = datetime.now(UTC)
    start = end - timedelta(days=lookback_days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    df = fetch_full_history(symbol, "1h", start_ms, end_ms, sleep_per_call=0.15)
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).sort_index()
    if timeframe != "1h":
        df = resample_ohlcv(df, timeframe)
    return df


TF_PPY = {"1h": 24*365, "2h": 12*365, "4h": 6*365, "8h": 3*365, "1d": 365}


LIVE_GUARD_STATE_FILE = REPO_ROOT / "data" / "live_guard" / "state.json"
# EMA smoothing α for sr_6m. Damps single-bar swings (observed ±0.24 pt/h
# in BTC near threshold — caused premature PARK transitions). α=0.3 means
# one outlier has 30% impact and decays within ~6 bars.
LIVE_GUARD_EMA_ALPHA = 0.3


def _load_live_guard_state() -> dict:
    try:
        with open(LIVE_GUARD_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_live_guard_state(state: dict) -> None:
    LIVE_GUARD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LIVE_GUARD_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def evaluate_live_guard(
    cfg: EngineConfig,
    pnl: np.ndarray,
    ppy: int,
    symbol: str | None = None,
) -> tuple[str, float, float]:
    """Compute EMA-smoothed 6M rolling Sharpe and live-guard verdict.

    Returns (verdict, multiplier, sharpe_6m_smoothed) where:
        verdict ∈ {"ACTIVE", "WARN", "PARKED", "INSUFFICIENT_DATA"}
        multiplier ∈ [0.0, 1.0] to apply to position size.

    EMA smoothing prevents flapping. Raw sr_6m near thresholds was
    observed to swing ±0.24 pt/hour due to 4320-bar window statistical
    noise. The threshold is applied to the smoothed value instead.
    """
    if not cfg.live_guard_enabled:
        return "DISABLED", 1.0, float("nan")

    bars_6m = int(cfg.live_guard_lookback_days * ppy / 365)
    recent = pnl[-bars_6m:] if len(pnl) >= bars_6m else pnl
    if len(recent) < cfg.live_guard_min_bars:
        return "INSUFFICIENT_DATA", 1.0, float("nan")

    sr_6m_raw = sharpe_ratio(recent, periods_per_year=ppy)
    if not np.isfinite(sr_6m_raw):
        return "INSUFFICIENT_DATA", 1.0, float("nan")

    # EMA smoothing (per symbol, persisted across hourly invocations).
    sr_for_threshold = float(sr_6m_raw)
    if symbol is not None:
        state = _load_live_guard_state()
        prior = state.get(symbol, {}).get("sr_6m_ema")
        if prior is not None:
            sr_for_threshold = (
                LIVE_GUARD_EMA_ALPHA * float(sr_6m_raw)
                + (1 - LIVE_GUARD_EMA_ALPHA) * float(prior)
            )
        state[symbol] = {
            "sr_6m_raw": float(sr_6m_raw),
            "sr_6m_ema": sr_for_threshold,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        _save_live_guard_state(state)

    if sr_for_threshold < cfg.live_guard_park_threshold:
        return "PARKED", 0.0, float(sr_for_threshold)
    if sr_for_threshold < cfg.live_guard_warn_threshold:
        return "WARN", 0.5, float(sr_for_threshold)
    return "ACTIVE", 1.0, float(sr_for_threshold)


def run_engine(
    symbol: str,
    df: pd.DataFrame,
    cfg: EngineConfig,
    timeframe: str = "1h",
    apply_live_guard: bool = True,
) -> dict:
    """Run v4.3 per-symbol engine, return current signal + diagnostics."""
    ret = df["close"].pct_change().fillna(0.0)
    ppy = TF_PPY.get(timeframe, 24 * 365)

    # Config-level park check first
    config_parked, park_reason = is_symbol_parked(cfg, symbol)
    if config_parked:
        last_close = float(df["close"].iloc[-1])
        last_time = str(df.index[-1])
        return {
            "symbol": symbol,
            "timestamp": last_time,
            "price": last_close,
            "regime": "N/A",
            "target_position": 0.0,
            "position_change": 0.0,
            "action": "PARK",
            "parked": True,
            "live_guard": "CONFIG_PARKED",
            "parked_reason": park_reason,
            "rolling_30d_sharpe": None,
            "live_6m_sharpe": None,
            "alpha_positions": {},
            "alpha_weights": {},
        }

    # Per-symbol alpha set
    alpha_names, alpha_params = alphas_for_symbol(cfg, symbol)
    if not alpha_names:
        return {
            "symbol": symbol,
            "error": "no alphas configured and not explicitly parked",
            "parked": True,
            "live_guard": "NO_ALPHAS",
            "target_position": 0.0,
        }

    # Build positions
    alpha_pos: dict[str, pd.Series] = {}
    alpha_current: dict[str, float | str] = {}
    for name in alpha_names:
        try:
            cfg_a = AlphaConfig(name=name, params=alpha_params.get(name, {}))
            alpha = get_alpha(name, cfg_a, symbol=symbol)
            pos = alpha.generate(df).position
            alpha_pos[name] = pos
            alpha_current[name] = float(pos.iloc[-1])
        except Exception as exc:
            alpha_current[name] = f"error: {exc}"

    if not alpha_pos:
        return {"symbol": symbol, "error": "no alphas generated (all failed)"}

    # Regime
    regime = VolTrendRegime().fit_predict(df)
    current_regime = regime.proba.iloc[-1].idxmax() if regime.proba is not None else "?"

    # Choppiness regime dampener (v4.3)
    rr_alpha = RangeReversionAlpha()
    chop_score = rr_alpha.get_regime_score(df)
    chop_damping = 1.0 - 0.5 * chop_score
    for name in list(alpha_pos.keys()):
        alpha_pos[name] = alpha_pos[name] * chop_damping
    current_chop = float(chop_score.iloc[-1])

    # Ensemble
    dz = cfg.turnover_deadzone if timeframe == "1h" else 0.05
    ens_cfg = EnsembleConfig(
        combine_mode=cfg.combine_mode,
        periods_per_year=ppy,
        turnover_deadzone=dz,
        sizing_mode=cfg.sizing_mode,
        kelly_fraction=cfg.kelly_fraction,
    )
    res = EnsembleAllocator(ens_cfg).combine(
        alpha_pos, ret, regime_proba=regime.proba, regime_alpha_affinity=AFFINITY
    )
    pos = res.target_position.fillna(0.0)
    # Per-symbol long-only restriction (asset_overrides.<SYM>.clip_short).
    # When true, negative ensemble positions are clamped to 0 before sizing/guard.
    if cfg.asset_overrides.get(symbol, {}).get("clip_short"):
        pos = pos.clip(lower=0.0)
    current_pos = float(pos.iloc[-1])
    prev_pos = float(pos.iloc[-2]) if len(pos) > 1 else 0.0

    # Rolling performance
    pnl = (pos.shift(1).fillna(0.0) * ret).values  # realized (shift prevents lookahead)
    bars_30d = int(30 * ppy / 365)
    recent_30d = pnl[-bars_30d:] if len(pnl) >= bars_30d else pnl
    recent_sharpe = sharpe_ratio(recent_30d, periods_per_year=ppy)

    # Live guard
    guard_verdict, guard_mult, sr_6m = evaluate_live_guard(cfg, pnl, ppy, symbol=symbol) if apply_live_guard else ("DISABLED", 1.0, float("nan"))

    guarded_pos = current_pos * guard_mult
    guarded_delta = guarded_pos - prev_pos

    last_close = float(df["close"].iloc[-1])
    last_time = str(df.index[-1])

    # Action label
    if guard_verdict == "PARKED":
        action = "PARK"
    elif abs(guarded_delta) > 0.02:
        action = "BUY" if guarded_delta > 0 else "SELL"
    else:
        action = "HOLD"

    return {
        "symbol": symbol,
        "timestamp": last_time,
        "price": last_close,
        "regime": current_regime,
        "choppiness": round(current_chop, 3),
        "raw_target_position": round(current_pos, 4),
        "target_position": round(guarded_pos, 4),
        "position_change": round(guarded_delta, 4),
        "action": action,
        "parked": guard_verdict == "PARKED",
        "live_guard": guard_verdict,
        "live_guard_multiplier": round(guard_mult, 2),
        "live_6m_sharpe": None if not np.isfinite(sr_6m) else round(float(sr_6m), 2),
        "rolling_30d_sharpe": round(float(recent_sharpe), 2) if np.isfinite(recent_sharpe) else None,
        "alpha_positions": {k: round(v, 4) if isinstance(v, float) else v for k, v in alpha_current.items()},
        "alpha_weights": {k: round(v, 4) for k, v in res.alpha_weights.iloc[-1].to_dict().items()} if len(res.alpha_weights) > 0 else {},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(REPO_ROOT / "config" / "v4_production.json"))
    ap.add_argument("--symbols", default=None, help="Override config symbols (comma-sep). Default: union of active + parked from config.")
    ap.add_argument("--lookback", type=int, default=220, help="days of history (need ≥180 for 6M Sharpe)")
    ap.add_argument("--timeframe", default="1h", choices=["1h", "4h", "8h"])
    ap.add_argument("--json", action="store_true", help="output as JSON")
    ap.add_argument("--no-live-guard", dest="live_guard", action="store_false", default=True)
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        # Union of active + parked (parked still produces signal with flat position)
        symbols = list(dict.fromkeys(list(cfg.symbols) + list(cfg.symbols_parked.keys())))
    tf = args.timeframe
    signals: list[dict] = []

    for symbol in symbols:
        print(f"Fetching {symbol} ({tf})...", end=" ", flush=True)
        try:
            df = fetch_recent(symbol, args.lookback, timeframe=tf)
            print(f"{len(df)} bars", flush=True)
            sig = run_engine(symbol, df, cfg, timeframe=tf, apply_live_guard=args.live_guard)
            signals.append(sig)
        except Exception as exc:
            print(f"ERROR: {exc}")
            signals.append({"symbol": symbol, "error": str(exc)})

    if args.json:
        print(json.dumps(signals, indent=2, default=str))
        return 0

    print("\n" + "=" * 70)
    print(f"  v4.3 LIVE SIGNALS — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    for sig in signals:
        if "error" in sig:
            print(f"\n  {sig['symbol']}: ERROR — {sig['error']}")
            continue

        sym = sig["symbol"]
        pos = sig.get("target_position", 0.0)
        action = sig.get("action", "?")
        guard = sig.get("live_guard", "?")
        sr_6m = sig.get("live_6m_sharpe")
        sr_30d = sig.get("rolling_30d_sharpe")

        guard_badge = {
            "ACTIVE": "✓",
            "WARN": "⚠",
            "PARKED": "⛔",
            "CONFIG_PARKED": "🔒",
            "INSUFFICIENT_DATA": "?",
            "DISABLED": "-",
            "NO_ALPHAS": "⛔",
        }.get(guard, "?")

        sr6_str = f"{sr_6m:+.2f}" if sr_6m is not None else "  n/a"
        sr30_str = f"{sr_30d:+.2f}" if sr_30d is not None else "  n/a"
        print(f"\n  {sym:10s} ${sig.get('price', 0):,.2f}  {guard_badge} {guard}")
        print(f"    pos={pos:+.4f}  Δ={sig.get('position_change', 0):+.4f}  action={action}")
        print(f"    6M-SR={sr6_str}  30d-SR={sr30_str}  regime={sig.get('regime', '?')}")
        if sig.get("parked") and sig.get("parked_reason"):
            print(f"    parked: {sig['parked_reason']}")

    # Summary
    print("\n" + "-" * 70)
    active = [s for s in signals if "error" not in s and not s.get("parked", False)]
    parked = [s for s in signals if s.get("parked", False)]
    positions = {s["symbol"]: s.get("target_position", 0.0) for s in active}
    net_exposure = sum(positions.values())
    print(f"  Active symbols: {len(active)}  Parked: {len(parked)}  Net exposure: {net_exposure:+.2f}")

    sharpes = [s.get("rolling_30d_sharpe") for s in active if s.get("rolling_30d_sharpe") is not None]
    avg_sh = np.mean(sharpes) if sharpes else 0.0
    health = "HEALTHY" if avg_sh > 0.5 else "CAUTION" if avg_sh > 0 else "DANGER"
    print(f"  Engine health: {health} (avg 30d Sharpe = {avg_sh:+.2f})")
    print("=" * 70)

    out_path = REPO_ROOT / "data" / "signals" / f"signals_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(signals, f, indent=2, default=str)
    print(f"\nSaved to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
