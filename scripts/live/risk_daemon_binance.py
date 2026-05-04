#!/usr/bin/env python3
"""실시간 리스크 데몬 — Binance USDT-M Futures.

Upbit 데몬(scripts/live/risk_daemon.py)의 Binance Futures 버전.
동일 임계값/HALT 메커니즘/Telegram 알림을 사용하되 다음만 다름:

  * WebSocket: wss://fstream.binance.com/ws (mainnet) /
               wss://stream.binancefuture.com/ws (testnet)
    → @aggTrade 스트림 구독, message {e,s,p,T} 파싱
  * 포지션: get_positions() — long/short 모두 가능. 청산 시 long→SELL,
    short→BUY (long-only 가정인 v4.5에선 사실상 long만 보임)
  * 심볼: BTCUSDT 형식 (KRW-BTC 아님)
  * 잔고 자산은 USDT (KRW 아님)

Helper 재사용:
  scripts.live.risk_daemon에서 set_halt / is_halted / clear_halt /
  PriceTracker / HALT_FLAG 를 그대로 import — halt.flag 파일은 봉
  스케줄러가 이미 검사하므로 통합됨.

Usage:
  python3 scripts/live/risk_daemon_binance.py \\
      --api-key KEY --api-secret SECRET [--testnet]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.execution.binance_futures import BinanceFuturesConnector  # noqa: E402
from shared.notifications.telegram import TelegramNotifier  # noqa: E402

# Reuse existing helpers — same halt.flag file, same PriceTracker math
from scripts.live.risk_daemon import (  # noqa: E402
    set_halt,
    is_halted,
    PriceTracker,
    FLASH_CRASH_PCT,
    FLASH_CRASH_WINDOW_SEC,
    HALT_DD_PCT,
    DAILY_LOSS_LIMIT_PCT,
)


BINANCE_WS_MAINNET = "wss://fstream.binance.com/ws"
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com/ws"
PING_INTERVAL = 30
DEFAULT_WATCH = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]


def _ws_url(testnet: bool) -> str:
    return BINANCE_WS_TESTNET if testnet else BINANCE_WS_MAINNET


async def liquidate_all(
    connector: BinanceFuturesConnector,
    notifier: TelegramNotifier,
    reason: str,
) -> None:
    """모든 보유 포지션 시장가 청산. long→SELL, short→BUY."""
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] 🚨 LIQUIDATING ALL: {reason}")
    if notifier.enabled:
        notifier.send(f"🚨 BINANCE EMERGENCY LIQUIDATION\nReason: {reason}")

    try:
        positions = connector.get_positions()  # {symbol: signed_qty}
        for symbol, qty in positions.items():
            if abs(qty) < 1e-10:
                continue
            side = "SELL" if qty > 0 else "BUY"
            close_qty = abs(qty)
            result = connector.place_market_order(symbol, side, close_qty)
            status = getattr(result, "status", "?")
            print(f"  {symbol}: {side} {close_qty:.6f} → {status}")
            if notifier.enabled:
                notifier.send(f"  {side} {symbol} {close_qty:.6f} → {status}")
    except Exception as e:
        print(f"  ❌ Liquidation error: {e}")
        if notifier.enabled:
            notifier.send(f"❌ Binance liquidation error: {e}")

    set_halt(reason)


async def equity_loop(
    connector: BinanceFuturesConnector,
    tracker: PriceTracker,
    interval: int = 60,
):
    """주기적으로 totalWalletBalance 갱신."""
    while True:
        try:
            eq = connector.get_account_equity()
            if tracker.equity_at_start is None:
                tracker.equity_at_start = eq
                print(f"  초기 equity: {eq:,.2f} USDT")
            tracker.equity_now = eq
        except Exception as e:
            print(f"  equity update failed: {e}")
        await asyncio.sleep(interval)


async def risk_monitor(
    connector: BinanceFuturesConnector,
    tracker: PriceTracker,
    notifier: TelegramNotifier,
    shutdown: asyncio.Event,
):
    """주기적으로 리스크 조건 체크."""
    while not shutdown.is_set():
        await asyncio.sleep(5)

        if tracker.equity_now is None or tracker.equity_at_start is None:
            continue

        # 1. 전체 DD
        dd = (tracker.equity_now - tracker.equity_at_start) / tracker.equity_at_start * 100
        if dd <= -HALT_DD_PCT:
            await liquidate_all(connector, notifier, f"portfolio_dd_{dd:.2f}%")
            shutdown.set()
            return

        # 2. Flash crash on any held symbol
        try:
            positions = connector.get_positions()
            for symbol, qty in positions.items():
                if abs(qty) < 1e-10:
                    continue
                crash = tracker.flash_crash(symbol, FLASH_CRASH_PCT)
                if crash is not None:
                    print(f"  ⚠️  FLASH CRASH {symbol}: {crash:.2f}%")
                    await liquidate_all(
                        connector, notifier,
                        f"flash_crash_{symbol}_{crash:.2f}%_in_{FLASH_CRASH_WINDOW_SEC}s",
                    )
                    shutdown.set()
                    return
        except Exception as e:
            print(f"  risk check failed: {e}")


async def ws_subscriber(
    symbols: list[str],
    tracker: PriceTracker,
    shutdown: asyncio.Event,
    testnet: bool = True,
):
    """Binance Futures aggTrade WebSocket 구독 → tracker 업데이트.

    Combined stream URL: wss://.../ws/<sym1>@aggTrade/<sym2>@aggTrade/...
    Each message: {e:"aggTrade", E:eventTimeMs, s:"BTCUSDT", p:"price", ...}
    """
    streams = "/".join(f"{s.lower()}@aggTrade" for s in symbols)
    url = f"{_ws_url(testnet)}/{streams}"
    while not shutdown.is_set():
        try:
            async with websockets.connect(url, ping_interval=PING_INTERVAL) as ws:
                print(f"  WebSocket connected ({'testnet' if testnet else 'mainnet'}): {symbols}")
                async for message in ws:
                    if shutdown.is_set():
                        break
                    try:
                        data = json.loads(message)
                        sym = data.get("s")
                        price = data.get("p")
                        ts_ms = data.get("E") or data.get("T") or 0
                        ts = (ts_ms / 1000.0) if ts_ms else datetime.now(timezone.utc).timestamp()
                        if sym and price is not None:
                            tracker.update_price(sym, float(price), float(ts))
                    except Exception as e:
                        print(f"  ws parse error: {e}")
        except Exception as e:
            print(f"  WebSocket disconnected: {e}. Reconnecting in 5s...")
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=5)
                break
            except asyncio.TimeoutError:
                continue


async def main_loop(args) -> None:
    notifier = TelegramNotifier()
    connector = BinanceFuturesConnector(
        api_key=args.api_key,
        api_secret=args.api_secret,
        testnet=args.testnet,
    )

    if is_halted():
        print("  ⚠️  halt.flag exists — daemon will start but won't add new entries; "
              "manual clear required to resume scheduler.")

    # Subscribe set: held positions + DEFAULT_WATCH
    try:
        positions = connector.get_positions()
        held = [s for s, q in positions.items() if abs(q) > 1e-10]
    except Exception as e:
        print(f"  cannot fetch positions: {e}")
        held = []

    watch = sorted(set(held + DEFAULT_WATCH))
    print(f"  Monitoring: {watch}")

    if notifier.enabled:
        notifier.send(
            f"🛡️  Binance risk daemon started "
            f"({'testnet' if args.testnet else 'MAINNET'}): {watch}"
        )

    tracker = PriceTracker()
    shutdown = asyncio.Event()

    def _sig_handler():
        print("\n  Signal received — shutting down...")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _sig_handler)

    await asyncio.gather(
        equity_loop(connector, tracker, interval=60),
        risk_monitor(connector, tracker, notifier, shutdown),
        ws_subscriber(watch, tracker, shutdown, testnet=args.testnet),
    )

    print("  Risk daemon exited.")
    if notifier.enabled:
        notifier.send("🛡️  Binance risk daemon stopped")


def main() -> int:
    global FLASH_CRASH_PCT, HALT_DD_PCT  # rebind in this module so our risk_monitor reads them

    parser = argparse.ArgumentParser(description="실시간 리스크 데몬 — Binance Futures")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--api-secret", required=True)
    parser.add_argument("--testnet", action="store_true", default=False,
                        help="Use Binance Futures testnet (default: mainnet)")
    parser.add_argument("--flash-crash-pct", type=float, default=FLASH_CRASH_PCT,
                        help="Override module default")
    parser.add_argument("--halt-dd-pct", type=float, default=HALT_DD_PCT,
                        help="Override module default")
    args = parser.parse_args()

    FLASH_CRASH_PCT = args.flash_crash_pct
    HALT_DD_PCT = args.halt_dd_pct

    try:
        asyncio.run(main_loop(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
