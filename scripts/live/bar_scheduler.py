#!/usr/bin/env python3
"""봉-정렬 스케줄러 — cron 대체.

cron은 임의 :05 시점에 깨우는데, 실제로 필요한 건 **봉이 닫힌 순간**.
이 데몬은 1h/8h 봉 경계 + 30초(거래소 데이터 안정화 버퍼)에 트리거.

핵심 차이점 vs cron:
  - cron :05 실행 → 봉이 +5분 지났어도 같은 봉 데이터 = 정보 동일
  - 이 스케줄러 :00:30 실행 → 갓 닫힌 봉 데이터 = 가장 빠른 반응

왜 :05가 아닌 :30인가?
  - Binance kline API는 봉 닫힘 후 약 10-20초 뒤에 최종 값 반영
  - 30초 버퍼로 데이터 안정화 + 주문 실행 시간 확보
  - 굳이 분 단위 랜덤화 필요 없음 (다른 거래자와 경쟁 안 함, bar-close는 모두가 보는 시점)

Usage:
  # Dry-run, 1h + 8h 스케줄
  python bar_scheduler.py --timeframes 1h,8h --dry-run

  # Upbit live:
  python bar_scheduler.py --exchange upbit --live \\
      --api-key $UPBIT_KEY --api-secret $UPBIT_SECRET \\
      --timeframes 8h

Signals:
  - SIGTERM/SIGINT → graceful shutdown (현재 실행 중인 tick 완료 후 종료)
  - 예외 발생 시 log + Telegram alert, 다음 tick 계속 (crash-resilient)
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.notifications.telegram import TelegramNotifier


HALT_FLAG = Path("/home/ubuntu/quant/data/state/halt.flag")
BUFFER_SECONDS = 30  # 봉 닫힘 후 대기 (데이터 안정화)
TIMEFRAME_SECONDS = {
    "1h": 3600,
    "4h": 4 * 3600,
    "8h": 8 * 3600,
    "1d": 24 * 3600,
}


def next_bar_close(tf: str, now: datetime | None = None) -> datetime:
    """다음 봉 닫힘 시각 (UTC).

    예: now=09:23 UTC, tf=1h → 10:00:00 UTC
    예: now=07:59 UTC, tf=8h → 08:00:00 UTC (UTC 00/08/16 경계)
    """
    now = now or datetime.now(timezone.utc)
    secs = TIMEFRAME_SECONDS[tf]
    epoch = int(now.timestamp())
    next_epoch = ((epoch // secs) + 1) * secs
    return datetime.fromtimestamp(next_epoch, tz=timezone.utc)


async def run_once(cmd: list[str], tf: str, notifier: TelegramNotifier) -> None:
    """하나의 tick 실행: subprocess로 execute_signals 호출.

    HALT 플래그가 설정돼 있으면 skip (리스크 데몬이 긴급 정지시킨 상태).
    """
    start = datetime.now(timezone.utc)

    if HALT_FLAG.exists():
        print(f"[{start:%H:%M:%S}] ⚠️  HALT flag set — skipping {tf} tick")
        return

    print(f"[{start:%Y-%m-%d %H:%M:%S UTC}] TICK ({tf}) → {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        output = stdout.decode(errors="replace")

        # 로그 디렉터리에 기록
        log_dir = Path("/home/ubuntu/quant/data/logs/scheduler")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"tick_{tf}_{start:%Y%m%d}.log"
        with log_file.open("a") as f:
            f.write(f"\n===== {start:%H:%M:%S} (rc={proc.returncode}) =====\n")
            f.write(output)

        if proc.returncode != 0:
            err = f"❌ {tf} tick failed (rc={proc.returncode})"
            print(err)
            if notifier.enabled:
                notifier.send(f"{err}\n\n{output[-500:]}")
        else:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            print(f"  ✓ {tf} tick ok ({elapsed:.1f}s)")

    except asyncio.TimeoutError:
        print(f"  ❌ {tf} tick TIMEOUT (600s)")
        if notifier.enabled:
            notifier.send(f"❌ {tf} tick timeout after 600s")
    except Exception as e:
        print(f"  ❌ {tf} tick exception: {e}")
        if notifier.enabled:
            notifier.send(f"❌ {tf} tick exception: {e}")


async def scheduler_loop(
    timeframes: list[str], cmd_template: list[str], shutdown_event: asyncio.Event,
) -> None:
    """메인 루프: 가장 가까운 봉 닫힘까지 대기 → 트리거 → 반복."""
    notifier = TelegramNotifier()
    if notifier.enabled:
        notifier.send(f"🚀 Bar scheduler started: tf={timeframes}")

    while not shutdown_event.is_set():
        now = datetime.now(timezone.utc)
        # 각 timeframe의 다음 닫힘 시각 계산
        upcoming = [(tf, next_bar_close(tf, now)) for tf in timeframes]
        # 가장 빠른 닫힘 시점부터 처리
        upcoming.sort(key=lambda x: x[1])

        tf, close_time = upcoming[0]
        target = close_time + timedelta(seconds=BUFFER_SECONDS)
        wait = (target - now).total_seconds()

        print(f"  다음 tick: {tf} @ {target:%H:%M:%S UTC} (in {wait:.0f}s)")

        # shutdown_event를 대기하면서 중단 가능
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=max(wait, 0.1))
            break  # shutdown received
        except asyncio.TimeoutError:
            pass  # 정상적으로 대기 완료

        # 동시에 여러 tf가 닫히는 순간 (예: 08:00 UTC는 1h+8h 동시)
        # → 같은 close_time을 가진 모든 tf를 함께 실행
        due_now = [t for t, c in upcoming if c == close_time]
        for due_tf in due_now:
            cmd = cmd_template + ["--timeframe", due_tf]
            await run_once(cmd, due_tf, notifier)

    if notifier.enabled:
        notifier.send("🛑 Bar scheduler stopped")


def build_cmd_template(args) -> list[str]:
    """execute_signals.py 호출용 베이스 커맨드."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts/live/execute_signals.py"),
        "--exchange", args.exchange,
    ]
    if args.live:
        cmd.append("--live")
    elif args.testnet:
        cmd.append("--testnet")
    else:
        cmd.append("--dry-run")

    if args.api_key:
        cmd.extend(["--api-key", args.api_key])
    if args.api_secret:
        cmd.extend(["--api-secret", args.api_secret])
    # --timeframe은 scheduler_loop에서 붙임
    return cmd


def main():
    parser = argparse.ArgumentParser(
        description="봉-정렬 실행 스케줄러 (cron 대체)",
    )
    parser.add_argument(
        "--timeframes", default="8h",
        help="콤마 구분. 예: 1h,8h (default: 8h)",
    )
    parser.add_argument(
        "--exchange", default="binance",
        choices=["binance", "upbit"],
    )
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-secret", default="")
    args = parser.parse_args()

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    for tf in timeframes:
        if tf not in TIMEFRAME_SECONDS:
            print(f"ERROR: unknown timeframe '{tf}'. Valid: {list(TIMEFRAME_SECONDS)}")
            return 1

    # execute_signals.py는 아직 --timeframe 인자를 받지 않음
    # → 일단 기본값으로 호출. 향후 timeframe별 다른 설정 필요 시 확장.
    cmd_template = build_cmd_template(args)

    shutdown_event = asyncio.Event()

    def _sigterm_handler():
        print("\n  SIGTERM received — graceful shutdown...")
        shutdown_event.set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _sigterm_handler)

    try:
        loop.run_until_complete(scheduler_loop(timeframes, cmd_template, shutdown_event))
    finally:
        loop.close()

    print("  Scheduler exited cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
