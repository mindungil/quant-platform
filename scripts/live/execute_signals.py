#!/usr/bin/env python3
"""DEPRECATED — do NOT run this script.

Legacy v4.1 executor that hardcodes a 4-alpha list (kalman/momentum/
vol_breakout/trend_breakout) — which IGNORES config/v4_production.json
and is missing funding_carry (the v4.4 addition). It was the root cause
of the -10.2% paper bleed discovered 2026-04-24 because it bypassed
the config-driven alpha selection.

Replacements (all config-driven, respect live_guard + parked flags):
  • scripts/live/generate_signals.py         — writes signal JSONs
  • scripts/live/signal_to_order_bridge.py   — consumes signal JSONs,
                                              runs dry/virtual/testnet/live

This file is retained only so the git history is intact; any execution
attempt raises immediately. If you need the Upbit code path, port it
into signal_to_order_bridge.py as a new `--upbit` mode.
"""
from __future__ import annotations

import sys

print(
    "\n  ✗ execute_signals.py is DEPRECATED (v4.1 hardcoded alphas — no funding_carry).\n"
    "    Use scripts/live/signal_to_order_bridge.py instead.\n"
    "    See docstring at the top of this file for details.\n",
    file=sys.stderr,
)
sys.exit(2)

# ---- everything below is retained for history reference only ----
# The unreachable code that follows is the old v4.1 pipeline.

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd

from shared.alpha.base import AlphaConfig
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha
from shared.alpha.trend_breakout import TrendBreakoutAlpha
from shared.alpha.vol_breakout import VolBreakoutAlpha
from shared.execution.binance_futures import BinanceFuturesConnector
from shared.execution.upbit import UpbitConnector
from shared.execution.position_tracker import PositionTracker
from shared.execution.order_executor import OrderExecutor
from shared.execution.risk_limits import RiskLimits
from shared.notifications.telegram import TelegramNotifier


# Upbit 현물: KRW 마켓에서 거래되는 메이저 코인
UPBIT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
BINANCE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

# Production 알파 세트 (long-only eval에서 SR > 0.3 검증).
# kalman_trend removed 2026-05-04: demoted in registry 2026-04-30 (6M SR -1.82).
# generate_signals.py + signal_to_order_bridge are the canonical live signal
# path; this list is only used by Upbit-spot dry-runs via bar_scheduler.
ALPHAS = [
    (MomentumEnsembleAlpha, "momentum", {}),   # default params (walk-forward tuned)
    (VolBreakoutAlpha, "vol_breakout", {}),
    (TrendBreakoutAlpha, "trend", {"donchian_window": 15, "exit_window": 7}),
]


def fetch_recent_8h(symbol: str, bars: int = 200) -> pd.DataFrame:
    """Fetch recent 8h bars from Binance public API (signal generation용).

    시그널 생성은 Binance USDT 가격으로도 충분 — 알파는 가격의 상대 패턴만 봄.
    실제 주문 notional 계산은 Upbit KRW 가격으로 별도 해야 함.
    """
    import urllib.request
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval=8h&limit={bars}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "quant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    rows = []
    for k in data:
        ts = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
        rows.append({
            "timestamp": ts,
            "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]),
            "volume": float(k[5]),
        })
    return pd.DataFrame(rows).set_index("timestamp").sort_index()


def fetch_upbit_krw_prices(symbols: list[str]) -> dict[str, float]:
    """Upbit 공개 ticker에서 KRW 가격 조회 (dry-run용)."""
    import urllib.request
    markets = []
    for s in symbols:
        asset = s.upper().replace("USDT", "").replace("USD", "").replace("KRW", "")
        markets.append(f"KRW-{asset}")

    url = f"https://api.upbit.com/v1/ticker?markets={','.join(markets)}"
    req = urllib.request.Request(url, headers={"User-Agent": "quant/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    prices: dict[str, float] = {}
    for item in data:
        market = item["market"]
        asset = market.split("-", 1)[1]
        prices[f"{asset}USDT"] = float(item["trade_price"])
    return prices


def generate_target_positions(
    equity: float,
    symbols: list[str],
    *,
    long_only: bool = False,
    price_currency: str = "USD",
    prices_override: dict[str, float] | None = None,
) -> dict[str, float]:
    """Target positions per symbol.

    long_only=True (Upbit): 음수 시그널 → 0 (flat). 현금 대기.
    price_currency: equity와 동일 통화로 가격을 가져와야 단위 일치.
        - Binance Futures: USD (default)
        - Upbit: KRW (prices_override로 Upbit ticker 전달)
    """
    target: dict[str, float] = {}
    per_symbol_alloc = equity / max(len(symbols), 1)

    # Upbit 실행 시 KRW 가격을 사전 조회해두면 per-symbol HTTP round-trip 절약.
    krw_prices = prices_override or {}
    if price_currency == "KRW" and not krw_prices:
        try:
            krw_prices = fetch_upbit_krw_prices(symbols)
        except Exception as e:
            print(f"  WARNING: Upbit KRW 가격 조회 실패 — {e}")

    for symbol in symbols:
        try:
            df = fetch_recent_8h(symbol)
            positions: list[pd.Series] = []
            for cls, name, params in ALPHAS:
                sig = cls(AlphaConfig(name=name, params=params)).generate(df)
                positions.append(sig.position)

            ensemble = sum(positions) / len(positions)
            latest = float(ensemble.iloc[-1])
            if long_only and latest < 0:
                latest = 0.0

            # 실제 주문 사이즈는 equity 통화 기준 가격으로 계산
            if price_currency == "KRW":
                price = krw_prices.get(symbol, 0.0)
            else:
                price = float(df["close"].iloc[-1])

            if price > 0:
                qty = (latest * per_symbol_alloc) / price
                target[symbol] = round(qty, 6)
            else:
                target[symbol] = 0.0

        except Exception as e:
            print(f"  {symbol}: signal failed — {e}")
            target[symbol] = 0.0

    return target


def build_connector(exchange: str, api_key: str, api_secret: str, testnet: bool):
    """거래소별 connector factory."""
    if exchange == "binance":
        return BinanceFuturesConnector(
            api_key=api_key, api_secret=api_secret, testnet=testnet,
        )
    elif exchange == "upbit":
        if testnet:
            print("  WARNING: Upbit는 testnet 미제공. 실계좌로 동작.")
        return UpbitConnector(api_key=api_key, api_secret=api_secret)
    else:
        raise ValueError(f"Unknown exchange: {exchange}")


def main():
    parser = argparse.ArgumentParser(description="Execute trading signals")
    parser.add_argument(
        "--exchange", default="binance",
        choices=["binance", "upbit"],
        help="거래소 (default: binance)",
    )
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true", help="실계좌 실행 (실제 돈)")
    parser.add_argument("--testnet", action="store_true", help="Binance testnet only")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-secret", default="")
    parser.add_argument(
        "--equity-override", type=float, default=None,
        help="Dry-run equity 수동 지정 (default: 10000)",
    )
    args = parser.parse_args()

    is_dry_run = not (args.live or args.testnet)
    use_testnet = args.testnet and not args.live
    exchange = args.exchange.lower()

    # Upbit은 long-only 강제
    long_only = exchange == "upbit"

    # 거래소별 symbol universe
    symbols = UPBIT_SYMBOLS if exchange == "upbit" else BINANCE_SYMBOLS

    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] Execution starting...")
    print(f"  Exchange: {exchange}")
    print(f"  Mode: {'DRY-RUN' if is_dry_run else 'TESTNET' if use_testnet else 'LIVE'}")
    print(f"  Long-only: {long_only}")
    print(f"  Symbols: {symbols}")

    notifier = TelegramNotifier()

    price_currency = "KRW" if exchange == "upbit" else "USD"

    if is_dry_run:
        equity = args.equity_override or (1_000_000.0 if exchange == "upbit" else 10_000.0)
        print(f"\n  Generating signals (equity={equity:,.0f} {price_currency})...")
        targets = generate_target_positions(
            equity, symbols, long_only=long_only, price_currency=price_currency,
        )

        print(f"\n  Target Positions:")
        for sym, qty in sorted(targets.items()):
            direction = "LONG" if qty > 0 else "SHORT" if qty < 0 else "FLAT"
            print(f"    {sym}: {direction} {qty:+.6f}")

        net = sum(targets.values())
        gross = sum(abs(q) for q in targets.values())
        print(f"\n  Net: {net:+.6f}  |  Gross: {gross:.6f}")
        print(f"\n  [DRY-RUN] No orders sent.")

        if notifier.enabled:
            notifier.signal_alert(targets)

    else:
        api_key, api_secret = args.api_key, args.api_secret
        if not api_key or not api_secret:
            # Fallback: env/파일에서 로드
            try:
                from shared.execution.credentials import load_credentials
                api_key, api_secret = load_credentials(exchange)
                print(f"  ✓ {exchange} 키를 credentials store에서 로드")
            except Exception as e:
                print(f"ERROR: API 키 없음. --api-key 지정 또는 credentials store 사용: {e}")
                return 1

        connector = build_connector(exchange, api_key, api_secret, use_testnet)

        equity = connector.get_account_equity()
        currency = "KRW" if exchange == "upbit" else "USD"
        print(f"  Account equity: {equity:,.0f} {currency}")

        if equity <= 0:
            print("ERROR: 잔고 없음. 충전 후 재시도.")
            return 1

        prices = connector.get_mark_prices(symbols)
        targets = generate_target_positions(
            equity, symbols,
            long_only=long_only,
            price_currency=price_currency,
            prices_override=prices if price_currency == "KRW" else None,
        )

        tracker = PositionTracker(connector, min_trade_notional=10)
        reconciliation = tracker.reconcile(targets, prices)

        print(f"\n  Orders needed: {len(reconciliation.orders_needed)}")
        for o in reconciliation.orders_needed:
            print(f"    {o.side} {o.symbol} {o.quantity:.6f}")

        # Upbit 수수료 모델: 5bps maker/taker
        limits = RiskLimits(
            max_position_per_symbol=0.25,
            max_total_exposure=1.0 if long_only else 1.5,  # long-only → gross ≤ 1
            max_drawdown_halt=0.15,
            min_order_size_usd=10.0,
        )
        executor = OrderExecutor(connector, risk_limits=limits, dry_run=False)
        result = executor.execute(
            reconciliation.orders_needed,
            equity=equity,
            current_positions={
                s: q * prices.get(s, 0)
                for s, q in reconciliation.actual_positions.items()
            },
            prices=prices,
        )

        print(f"\n  Execution: {result.orders_filled} filled, {result.orders_failed} failed")
        print(f"  Total notional: {result.total_notional:,.0f} {currency}")

        if notifier.enabled:
            notifier.execution_alert(
                result.orders_filled, result.orders_failed, result.total_notional,
            )

    print(f"\n[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
