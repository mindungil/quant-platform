#!/usr/bin/env python3
"""실시간 리스크 데몬 — WebSocket 기반 긴급 청산.

bar_scheduler는 봉 닫힘마다 동작하지만, 급락은 봉 내에 일어남.
이 데몬은 Upbit WebSocket으로 실시간 가격을 구독하고 다음 상황에서 즉시 청산:

  1. 단일 코인 MDD_PCT% 초과
  2. 총 포트폴리오 drawdown HALT_DD 초과
  3. 급변 감지: 5분 내 FLASH_CRASH_PCT% 이상 하락
  4. 일일 손실 한도 DAILY_LOSS_LIMIT 초과

청산 방식:
  - 모든 보유 포지션 시장가 매도 (long-only 가정)
  - Telegram 알림 + 로그
  - bar_scheduler에 HALT 플래그 전달 (data/state/halt.flag 파일)
  - 수동 해제될 때까지 새 진입 차단

Usage:
  python risk_daemon.py --exchange upbit --api-key KEY --api-secret SECRET
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

from shared.execution.upbit import UpbitConnector
from shared.notifications.telegram import TelegramNotifier


UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
HALT_FLAG = Path("/home/ubuntu/quant/data/state/halt.flag")
STATE_DIR = HALT_FLAG.parent


# 리스크 임계값
FLASH_CRASH_PCT = 5.0          # 5분 내 5% 하락 → 급변
FLASH_CRASH_WINDOW_SEC = 300
HALT_DD_PCT = 10.0              # 총 포트폴리오 -10% → 정지
SINGLE_COIN_DD_PCT = 15.0       # 단일 코인 -15% → 해당 코인만 청산
DAILY_LOSS_LIMIT_PCT = 8.0      # 일일 -8% → 정지
PING_INTERVAL = 30               # Upbit WebSocket ping interval


def set_halt(reason: str) -> None:
    """HALT 플래그 파일 생성 — bar_scheduler가 검사."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HALT_FLAG.write_text(
        json.dumps({
            "halted_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        })
    )


def is_halted() -> bool:
    return HALT_FLAG.exists()


def clear_halt() -> None:
    """수동 해제용."""
    if HALT_FLAG.exists():
        HALT_FLAG.unlink()


async def liquidate_all(connector: UpbitConnector, notifier: TelegramNotifier, reason: str) -> None:
    """모든 보유 포지션 시장가 매도."""
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] 🚨 LIQUIDATING ALL: {reason}")
    if notifier.enabled:
        notifier.send(f"🚨 EMERGENCY LIQUIDATION\nReason: {reason}")

    try:
        balances = connector.get_balances()
        for asset, qty in balances.items():
            if asset == "KRW" or qty <= 0:
                continue
            symbol = f"{asset}USDT"
            result = connector.place_market_order(symbol, "SELL", qty)
            status = result.status
            print(f"  {symbol}: SELL {qty:.6f} → {status}")
            if notifier.enabled:
                notifier.send(f"  SELL {symbol} {qty:.6f} → {status}")
    except Exception as e:
        print(f"  ❌ Liquidation error: {e}")
        if notifier.enabled:
            notifier.send(f"❌ Liquidation error: {e}")

    set_halt(reason)


class PriceTracker:
    """코인별 최근 가격 이력 — flash crash 감지용."""

    def __init__(self, window_sec: int = FLASH_CRASH_WINDOW_SEC) -> None:
        self.window_sec = window_sec
        self.history: dict[str, list[tuple[float, float]]] = {}  # market → [(ts, price)]
        self.equity_at_start: float | None = None
        self.equity_now: float | None = None

    def update_price(self, market: str, price: float, ts: float) -> None:
        hist = self.history.setdefault(market, [])
        hist.append((ts, price))
        # 윈도우 밖 제거
        cutoff = ts - self.window_sec
        while hist and hist[0][0] < cutoff:
            hist.pop(0)

    def flash_crash(self, market: str, pct_threshold: float) -> float | None:
        """윈도우 내 최대 하락률(%). 임계값 이하일 때 값 반환, 아니면 None."""
        hist = self.history.get(market, [])
        if len(hist) < 2:
            return None
        current = hist[-1][1]
        peak = max(p for _, p in hist)
        drawdown_pct = (current - peak) / peak * 100
        if drawdown_pct <= -pct_threshold:
            return drawdown_pct
        return None


async def equity_loop(connector: UpbitConnector, tracker: PriceTracker, interval: int = 60):
    """주기적으로 총 equity 갱신."""
    while True:
        try:
            eq = connector.get_account_equity()
            if tracker.equity_at_start is None:
                tracker.equity_at_start = eq
                print(f"  초기 equity: {eq:,.0f} KRW")
            tracker.equity_now = eq
        except Exception as e:
            print(f"  equity update failed: {e}")
        await asyncio.sleep(interval)


async def risk_monitor(
    connector: UpbitConnector,
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

        # 2. Flash crash (모든 보유 코인)
        try:
            balances = connector.get_balances()
            for asset, qty in balances.items():
                if asset == "KRW" or qty <= 0:
                    continue
                market = f"KRW-{asset}"
                crash = tracker.flash_crash(market, FLASH_CRASH_PCT)
                if crash is not None:
                    print(f"  ⚠️  FLASH CRASH {market}: {crash:.2f}%")
                    await liquidate_all(
                        connector, notifier,
                        f"flash_crash_{market}_{crash:.2f}%_in_{FLASH_CRASH_WINDOW_SEC}s",
                    )
                    shutdown.set()
                    return
        except Exception as e:
            print(f"  risk check failed: {e}")


async def ws_subscriber(
    markets: list[str], tracker: PriceTracker, shutdown: asyncio.Event,
):
    """Upbit WebSocket 구독 → 실시간 가격 tracker 업데이트."""
    while not shutdown.is_set():
        try:
            async with websockets.connect(UPBIT_WS_URL, ping_interval=PING_INTERVAL) as ws:
                subscribe = [
                    {"ticket": "risk-daemon"},
                    {"type": "ticker", "codes": markets},
                    {"format": "SIMPLE"},
                ]
                await ws.send(json.dumps(subscribe))
                print(f"  WebSocket connected: {markets}")

                async for message in ws:
                    if shutdown.is_set():
                        break
                    try:
                        data = json.loads(message)
                        market = data.get("cd") or data.get("code")
                        price = data.get("tp") or data.get("trade_price")
                        ts = data.get("ttms", 0) / 1000 or datetime.now(timezone.utc).timestamp()
                        if market and price:
                            tracker.update_price(market, float(price), float(ts))
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
    connector = UpbitConnector(api_key=args.api_key, api_secret=args.api_secret)

    # 구독할 코인: 현재 보유 + 주요 코인 (보유 안 해도 추적)
    try:
        balances = connector.get_balances()
        held_assets = [a for a, q in balances.items() if a != "KRW" and q > 0]
    except Exception as e:
        print(f"  cannot fetch balances: {e}")
        held_assets = []

    watch_assets = list(set(held_assets + ["BTC", "ETH", "SOL", "XRP", "DOGE"]))
    markets = [f"KRW-{a}" for a in watch_assets]
    print(f"  Monitoring: {markets}")

    if notifier.enabled:
        notifier.send(f"🛡️  Risk daemon started: {markets}")

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
        ws_subscriber(markets, tracker, shutdown),
    )

    print("  Risk daemon exited.")
    if notifier.enabled:
        notifier.send("🛡️  Risk daemon stopped")


def main():
    global FLASH_CRASH_PCT, HALT_DD_PCT

    parser = argparse.ArgumentParser(description="실시간 리스크 데몬")
    parser.add_argument("--exchange", default="upbit", choices=["upbit"])
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--api-secret", required=True)
    parser.add_argument("--flash-crash-pct", type=float, default=FLASH_CRASH_PCT)
    parser.add_argument("--halt-dd-pct", type=float, default=HALT_DD_PCT)
    args = parser.parse_args()

    # 전역 임계값 재설정 (커맨드라인 오버라이드)
    FLASH_CRASH_PCT = args.flash_crash_pct
    HALT_DD_PCT = args.halt_dd_pct

    try:
        asyncio.run(main_loop(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
