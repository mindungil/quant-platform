"""실행 원장 (execution ledger) — 영속적 감사 로그.

모든 주문/체결을 JSONL 파일에 append. 매 tick마다 거래소 상태와 원장을 대조.

재시작 시나리오:
  1. bar_scheduler → execute_signals 호출 중 crash
  2. 재시작 → reconcile()은 연결해서 exchange.get_positions() 실행
  3. 원장의 최근 주문과 exchange 상태가 다르면 alert

이 원장은 "진실 원천"이 아님. 진실은 거래소. 원장은 **감사**와 **debug**용.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from shared.execution.risk_limits import OrderResult, TradeOrder


LEDGER_DIR = Path("/home/ubuntu/quant/data/logs/ledger")


def _today_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return LEDGER_DIR / f"exec_{today}.jsonl"


def log_order(
    order: TradeOrder,
    result: OrderResult | None = None,
    *,
    tick_id: str = "",
    exchange: str = "",
) -> None:
    """주문/체결 이벤트를 JSONL 한 줄로 append."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ts_epoch": time.time(),
        "tick_id": tick_id,
        "exchange": exchange,
        "order": {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "order_type": order.order_type,
            "price": order.price,
        },
    }
    if result is not None:
        record["result"] = {
            "filled_quantity": result.filled_quantity,
            "avg_price": result.avg_price,
            "status": result.status,
            "order_id": result.order_id,
            "error": result.error,
        }

    with _today_path().open("a") as f:
        f.write(json.dumps(record) + "\n")


def recent_orders(hours: int = 24) -> list[dict]:
    """최근 N시간 내 주문 조회."""
    cutoff = time.time() - hours * 3600
    results: list[dict] = []

    today = datetime.now(timezone.utc)
    for delta in range(0, max(hours // 24 + 2, 2)):
        dt = today.replace(hour=0, minute=0, second=0, microsecond=0)
        dt = dt.fromtimestamp(dt.timestamp() - delta * 86400, tz=timezone.utc)
        path = LEDGER_DIR / f"exec_{dt.strftime('%Y%m%d')}.jsonl"
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("ts_epoch", 0) >= cutoff:
                        results.append(rec)
                except json.JSONDecodeError:
                    pass
    return results


def fill_summary(hours: int = 24) -> dict:
    """최근 체결 요약 통계."""
    orders = recent_orders(hours)
    n_total = len(orders)
    n_filled = sum(1 for o in orders if o.get("result", {}).get("status") == "FILLED")
    n_failed = sum(1 for o in orders if o.get("result", {}).get("status") in ("REJECTED", "ERROR"))
    n_pending = n_total - n_filled - n_failed

    total_notional = sum(
        (o.get("result", {}).get("filled_quantity", 0) or 0)
        * (o.get("result", {}).get("avg_price", 0) or 0)
        for o in orders
    )

    return {
        "hours": hours,
        "total_orders": n_total,
        "filled": n_filled,
        "failed": n_failed,
        "pending": n_pending,
        "fill_rate": n_filled / n_total if n_total else 0.0,
        "total_notional_krw": total_notional,
    }


def detect_drift(
    actual_positions: dict[str, float],
    target_positions: dict[str, float],
    tolerance_pct: float = 5.0,
) -> list[dict]:
    """target과 actual이 과도하게 다르면 drift 보고."""
    drifts: list[dict] = []
    all_syms = set(actual_positions) | set(target_positions)
    for sym in all_syms:
        target = target_positions.get(sym, 0.0)
        actual = actual_positions.get(sym, 0.0)
        if abs(target) < 1e-9 and abs(actual) < 1e-9:
            continue
        base = max(abs(target), abs(actual), 1e-9)
        pct_diff = abs(target - actual) / base * 100
        if pct_diff > tolerance_pct:
            drifts.append({
                "symbol": sym,
                "target": target,
                "actual": actual,
                "diff_pct": pct_diff,
            })
    return drifts
