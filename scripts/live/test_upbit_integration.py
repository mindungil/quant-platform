#!/usr/bin/env python3
"""Upbit 통합 테스트 — 실제 API 호출 전 검증.

Upbit sandbox 없음 → 검증 전략:
  1. Public API (dry-run 없이) — ticker/orderbook
  2. Dry-run 시그널 생성 end-to-end
  3. 저장된 키 유효성 검증 (validate_credentials)
  4. [수동] 소액 (5,000 KRW) 실주문 → 즉시 청산 (선택)

Usage:
  # Step 1-3 (키 없이 가능):
  python test_upbit_integration.py

  # Step 4 포함 (실제 주문):
  QUANT_MASTER_KEY=<key> python test_upbit_integration.py --live-test
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.execution.upbit import UpbitConnector, _to_market


def test_public_api() -> bool:
    """Public endpoints (키 불필요)."""
    print("[1/4] Public API 테스트...")
    conn = UpbitConnector("dummy", "dummy")

    try:
        prices = conn.get_mark_prices(["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"])
        for sym, p in prices.items():
            assert p > 0, f"{sym} price is 0"
            print(f"    ✓ {sym}: {p:,.0f} KRW")
    except Exception as e:
        print(f"    ❌ Ticker 실패: {e}")
        return False

    try:
        ob = conn._request("GET", "/v1/orderbook", params={"markets": "KRW-BTC"})
        best_bid = ob[0]["orderbook_units"][0]["bid_price"]
        best_ask = ob[0]["orderbook_units"][0]["ask_price"]
        spread_bps = (best_ask - best_bid) / best_bid * 1e4
        print(f"    ✓ BTC orderbook: bid={best_bid:,.0f}, ask={best_ask:,.0f}, spread={spread_bps:.1f}bps")
    except Exception as e:
        print(f"    ❌ Orderbook 실패: {e}")
        return False

    return True


def test_signal_dry_run() -> bool:
    """dry-run 시그널 생성 end-to-end."""
    print("\n[2/4] Dry-run 시그널 테스트...")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/live/execute_signals.py"),
         "--exchange", "upbit", "--dry-run", "--equity-override", "1000000"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"    ❌ execute_signals 실패:")
        print(result.stdout[-500:])
        print(result.stderr[-500:])
        return False

    # 출력 검증: Target Positions 섹션 존재
    if "Target Positions:" not in result.stdout:
        print(f"    ❌ 예상 출력 없음")
        return False

    print(f"    ✓ dry-run 성공, 시그널 생성 정상")
    # 실제 시그널 한 줄 보여주기
    lines = result.stdout.splitlines()
    in_section = False
    for line in lines:
        if "Target Positions:" in line:
            in_section = True
            continue
        if in_section and line.strip():
            if line.startswith("  ") and ("LONG" in line or "FLAT" in line or "SHORT" in line):
                print(f"      {line.strip()}")
            else:
                break
    return True


def test_credentials_check() -> bool:
    """저장된 키 또는 env 키 검증."""
    print("\n[3/4] Credential 로더 테스트...")
    try:
        from shared.execution.credentials import load_credentials
        key, secret = load_credentials("upbit")
        print(f"    ✓ 로드 성공: key={key[:8]}...{key[-4:]}")

        # 유효성 검증
        conn = UpbitConnector(key, secret)
        if conn.validate_credentials():
            print(f"    ✓ Upbit 키 유효")
            return True
        else:
            print(f"    ❌ Upbit 키 거부 (권한/만료/오타?)")
            return False
    except Exception as e:
        print(f"    ⚠️  credential 없음 — 건너뜀: {e}")
        return True  # non-fatal


def test_small_live_trade() -> bool:
    """실계좌 5,000 KRW 주문 → 즉시 청산."""
    print("\n[4/4] 소액 live 테스트 (5,000 KRW BTC 매수 → 매도)...")
    print("    ⚠️  실제 돈입니다. 3초 내 Ctrl+C로 취소.")
    for i in range(3, 0, -1):
        print(f"    {i}...", end="", flush=True)
        time.sleep(1)
    print()

    try:
        from shared.execution.credentials import load_credentials
        key, secret = load_credentials("upbit")
    except Exception as e:
        print(f"    ❌ credential 없음: {e}")
        return False

    conn = UpbitConnector(key, secret)

    # 잔고 확인
    try:
        balances = conn.get_balances()
        krw = balances.get("KRW", 0)
        print(f"    초기 KRW 잔고: {krw:,.0f}")
        if krw < 10_000:
            print(f"    ❌ 최소 10,000 KRW 필요 (현재 {krw:,.0f})")
            return False
    except Exception as e:
        print(f"    ❌ 잔고 조회 실패: {e}")
        return False

    # 최소 주문 5,000 KRW 이상 여유 있게 6,000 KRW로
    BTC_price = conn.get_mark_prices(["BTCUSDT"]).get("BTCUSDT", 0)
    if BTC_price <= 0:
        print("    ❌ BTC 가격 조회 실패")
        return False

    buy_qty = 6000 / BTC_price
    print(f"    BUY {buy_qty:.8f} BTC @ ~{BTC_price:,.0f} KRW ({buy_qty * BTC_price:,.0f} KRW 주문)")

    r_buy = conn.place_market_order("BTCUSDT", "BUY", buy_qty)
    print(f"    매수 결과: status={r_buy.status}, filled={r_buy.filled_quantity:.8f}, error={r_buy.error}")

    if r_buy.status in ("REJECTED", "ERROR"):
        print(f"    ❌ 매수 실패")
        return False

    # 2초 대기 후 보유량 확인
    time.sleep(2)
    balances_after = conn.get_balances()
    btc_held = balances_after.get("BTC", 0)
    print(f"    매수 후 BTC 보유: {btc_held:.8f}")

    if btc_held <= 0:
        print(f"    ❌ 매수 후 잔고 없음")
        return False

    # 전량 매도
    print(f"    SELL {btc_held:.8f} BTC")
    r_sell = conn.place_market_order("BTCUSDT", "SELL", btc_held)
    print(f"    매도 결과: status={r_sell.status}, filled={r_sell.filled_quantity:.8f}, error={r_sell.error}")

    # 최종 잔고
    time.sleep(2)
    final = conn.get_balances()
    final_krw = final.get("KRW", 0)
    cost = krw - final_krw  # 수수료 + slippage 등 총 비용
    print(f"    최종 KRW: {final_krw:,.0f} (왕복 비용: {cost:,.0f} KRW, {cost/krw*100:.3f}%)")

    return r_sell.status not in ("REJECTED", "ERROR")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-test", action="store_true",
                        help="실계좌 5,000원 주문 포함 (Step 4)")
    args = parser.parse_args()

    print("=" * 70)
    print("  Upbit 통합 테스트")
    print("=" * 70)

    checks = [
        ("public_api", test_public_api),
        ("signal_dry_run", test_signal_dry_run),
        ("credentials", test_credentials_check),
    ]
    if args.live_test:
        checks.append(("small_live_trade", test_small_live_trade))

    results = {}
    for name, fn in checks:
        results[name] = fn()

    print("\n" + "=" * 70)
    print("  결과 요약")
    print("=" * 70)
    for name, ok in results.items():
        marker = "✓" if ok else "❌"
        print(f"  {marker} {name}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
