#!/usr/bin/env python3
"""Live 계좌 P&L 리포트 — 일일 텔레그램 알림.

매일 자정 UTC + 15분에 실행:
  1. Upbit 계좌 현재 equity 조회
  2. baseline (data/state/baseline_equity.json) 대비 변화율 계산
  3. 최근 24시간 체결 요약 (ledger)
  4. 텔레그램 전송

baseline 파일이 없으면 현재 equity로 초기화.

Usage:
  python live_pnl_report.py --exchange upbit
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.execution.credentials import load_credentials
from shared.execution.ledger import fill_summary
from shared.execution.upbit import UpbitConnector
from shared.notifications.telegram import TelegramNotifier


BASELINE_FILE = REPO_ROOT / "data" / "state" / "baseline_equity.json"


def load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        return {}
    return json.loads(BASELINE_FILE.read_text())


def save_baseline(data: dict) -> None:
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exchange", default="upbit", choices=["upbit"])
    parser.add_argument("--reset-baseline", action="store_true",
                        help="오늘을 새 baseline으로 재설정")
    args = parser.parse_args()

    exchange = args.exchange

    try:
        key, secret = load_credentials(exchange)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    conn = UpbitConnector(key, secret)

    # 현재 equity
    try:
        equity_now = conn.get_account_equity()
    except Exception as e:
        print(f"ERROR: equity 조회 실패: {e}")
        return 1

    baseline = load_baseline()
    now_iso = datetime.now(timezone.utc).isoformat()

    if args.reset_baseline or exchange not in baseline:
        baseline[exchange] = {
            "initial_equity": equity_now,
            "initial_ts": now_iso,
            "peak_equity": equity_now,
        }
        save_baseline(baseline)
        print(f"  Baseline 설정: {equity_now:,.0f} KRW @ {now_iso}")
        return 0

    state = baseline[exchange]
    initial = state["initial_equity"]
    peak = max(state.get("peak_equity", initial), equity_now)
    state["peak_equity"] = peak
    save_baseline(baseline)

    total_return = (equity_now - initial) / initial * 100
    drawdown = (equity_now - peak) / peak * 100

    # 24h 체결 요약
    summary = fill_summary(hours=24)

    # 텔레그램 메시지 구성
    currency = "KRW"
    lines = [
        f"📊 <b>일일 P&L 리포트</b>",
        f"<i>{datetime.now(timezone.utc):%Y-%m-%d UTC}</i>",
        f"",
        f"💰 Equity: <b>{equity_now:,.0f} {currency}</b>",
        f"📈 누적 수익률: <b>{total_return:+.2f}%</b>",
        f"📉 Drawdown: {drawdown:.2f}%",
        f"🔝 Peak: {peak:,.0f} {currency}",
        f"",
        f"⚙️  24h 체결:",
        f"  전체 {summary['total_orders']}  |  성공 {summary['filled']}  |  실패 {summary['failed']}",
        f"  총 거래금액: {summary['total_notional_krw']:,.0f} {currency}",
    ]

    if drawdown < -10:
        lines.append("")
        lines.append(f"🚨 <b>DD &lt; -10% — 주의 필요</b>")
    elif drawdown < -5:
        lines.append("")
        lines.append(f"⚠️  DD -5% 초과")

    msg = "\n".join(lines)
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    notifier = TelegramNotifier()
    if notifier.enabled:
        notifier.send(msg)
        print("\n  텔레그램 전송 완료")
    else:
        print("\n  텔레그램 미설정 — 콘솔만 출력")

    return 0


if __name__ == "__main__":
    sys.exit(main())
