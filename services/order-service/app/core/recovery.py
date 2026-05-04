"""Startup recovery and active-order reconciliation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.db.repository import order_repository
from app.models.order import FillSnapshot, OrderRequest
from app.services.event_publisher import publisher
from app.services.exchange_client import ExchangeClient
from app.services.portfolio_client import PortfolioClient
from app.services.statistics_client import StatisticsClient
from app.core.config import settings
from shared.logging import get_logger

UTC = timezone.utc
logger = get_logger("order-service")

TERMINAL_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "FAILED", "SIMULATED_FILLED"}
ACTIVE_RECONCILE_STATUSES = {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"}

exchange_client = ExchangeClient(settings.exchange_adapter_base_url)
portfolio_client = PortfolioClient(settings.portfolio_service_base_url)
statistics_client = StatisticsClient(settings.statistics_service_base_url)


def _is_terminal(status: str | None) -> bool:
    normalized = (status or "").upper()
    return normalized in TERMINAL_STATUSES or normalized.startswith("REJECTED")


def _received_detail(order) -> dict:
    for item in order.lifecycle:
        detail = item.get("detail") or {}
        if detail.get("stage") == "received":
            return detail
    return {}


def _build_request_from_order(order) -> OrderRequest:
    received = _received_detail(order)
    fill_price = order.fill.filled_price if order.fill is not None else 0.0
    fill_quantity = order.fill.filled_quantity if order.fill is not None else 0.0
    quantity = float(order.quantity or fill_quantity or 0.0)
    price = float(fill_price or 0.0)
    requested_notional = price * quantity if price > 0 and quantity > 0 else 0.0
    return OrderRequest(
        user_id=order.user_id,
        exchange=order.exchange,
        asset=order.asset,
        side=order.side,
        quantity=quantity,
        price=price,
        requested_notional=requested_notional,
        max_notional=requested_notional or max(float(quantity or 0.0), 1.0),
        current_drawdown=0.0,
        current_exposure=0.0,
        exposure_limit=1.0,
        automation_enabled=True,
        shadow_mode=order.shadow_mode,
        strategy_id=received.get("strategy_id"),
        agent_name=received.get("agent_name"),
        lane=received.get("lane"),
        lane_budget_pct=received.get("lane_budget_pct"),
        subscription_id=received.get("subscription_id"),
        template_id=received.get("template_id"),
        correlation_id=order.order_id,
    )


def _latest_fill(fills: list[dict]) -> dict | None:
    if not fills:
        return None
    return fills[-1]


def reconcile_order(order) -> bool:
    if (order.status or "").upper() not in ACTIVE_RECONCILE_STATUSES:
        return False

    status_payload = exchange_client.get_status(order.order_id)
    if status_payload is None:
        return False

    fills = exchange_client.get_fills(order.order_id)
    latest_fill = _latest_fill(fills)
    next_status = (status_payload.get("status") or order.status or "").upper()
    previous_filled = float(order.fill.filled_quantity if order.fill is not None else 0.0)
    latest_filled = float((latest_fill or {}).get("filled_quantity", previous_filled) or previous_filled)
    latest_price = float((latest_fill or {}).get("average_fill_price", order.fill.filled_price if order.fill else 0.0) or 0.0)
    latest_fees = float((latest_fill or {}).get("fees", order.fill.fees if order.fill else 0.0) or 0.0)
    exchange_order_id = (latest_fill or {}).get("exchange_order_id") or (order.fill.exchange_order_id if order.fill else None)

    fill_delta = max(0.0, latest_filled - previous_filled)
    pre_fill_portfolio = None
    portfolio_snapshot = order.portfolio
    statistics_snapshot = order.statistics

    payload = _build_request_from_order(order)
    if fill_delta > 0 and latest_price > 0:
        pre_fill_portfolio = portfolio_client.get_snapshot(order.user_id)
        portfolio_data = portfolio_client.apply_fill(
            payload,
            order_id=order.order_id,
            status=next_status,
            fill_quantity=fill_delta,
            fill_price=latest_price,
            filled_notional=fill_delta * latest_price,
        )
        from app.models.order import PortfolioSnapshot, StatisticsSnapshot
        portfolio_snapshot = PortfolioSnapshot.model_validate(portfolio_data)
        statistics_data = statistics_client.record_trade(
            payload,
            order_status=next_status,
            order_id=order.order_id,
            pre_fill_portfolio=pre_fill_portfolio,
            fill_quantity=fill_delta,
            fill_price=latest_price,
        )
        statistics_snapshot = StatisticsSnapshot.model_validate(statistics_data)

    changed = fill_delta > 0 or next_status != (order.status or "").upper()
    if not changed:
        return False

    reconciled = order.model_copy(deep=True)
    reconciled.status = next_status
    if latest_filled > 0:
        reconciled.fill = FillSnapshot(
            order_id=order.order_id,
            status=next_status,
            filled_quantity=latest_filled,
            filled_price=latest_price,
            exchange_order_id=exchange_order_id,
            fees=latest_fees,
        )
    reconciled.portfolio = portfolio_snapshot
    reconciled.statistics = statistics_snapshot
    order_repository.save(
        order.user_id,
        reconciled,
        detail={
            "stage": "reconcile",
            "previous_status": order.status,
            "exchange_status": next_status,
            "previous_filled_quantity": previous_filled,
            "filled_quantity": latest_filled,
            "fill_delta": fill_delta,
            "source": "exchange_adapter",
        },
    )
    if fill_delta > 0 and reconciled.fill is not None:
        if next_status == "PARTIALLY_FILLED":
            publisher.publish_order_partially_filled(payload, reconciled)
        else:
            publisher.publish_order_filled(payload, reconciled)
    logger.info(
        "order_reconciled",
        extra={
            "service": "order-service",
            "order_id": order.order_id,
            "user_id": order.user_id,
            "previous_status": order.status,
            "status": next_status,
            "fill_delta": fill_delta,
            "event_type": "order.reconcile",
        },
    )
    return True


def recover_stuck_orders(max_age_seconds: int = 300) -> int:
    """Resolve stale orders during startup."""
    stuck = order_repository.find_stuck_orders(max_age_seconds=max_age_seconds)
    recovered = 0
    for order in stuck:
        if _is_terminal(order.status):
            continue
        try:
            if (order.status or "").upper() in ACTIVE_RECONCILE_STATUSES:
                if reconcile_order(order):
                    recovered += 1
                    continue
                order_repository.update_status(
                    order.order_id,
                    "FAILED",
                    detail="recovered_on_startup:submitted_no_response",
                )
            else:
                order_repository.update_status(
                    order.order_id,
                    "FAILED",
                    detail=f"recovered_on_startup:{(order.status or 'unknown').lower()}_stale",
                )
            recovered += 1
        except Exception:
            logger.exception(
                "order_recovery_failed",
                extra={
                    "service": "order-service",
                    "order_id": order.order_id,
                    "user_id": order.user_id,
                    "event_type": "order.recovery.error",
                },
            )
    return recovered


async def reconciliation_loop(*, poll_seconds: float = 15.0) -> None:
    """Continuously reconcile active orders with exchange-adapter truth."""
    while True:
        try:
            for order in order_repository.find_active_non_filled_orders():
                try:
                    reconcile_order(order)
                except Exception:
                    logger.exception(
                        "order_reconcile_loop_failed",
                        extra={
                            "service": "order-service",
                            "order_id": order.order_id,
                            "user_id": order.user_id,
                            "event_type": "order.reconcile.error",
                        },
                    )
        except Exception:
            logger.exception(
                "order_reconcile_scan_failed",
                extra={"service": "order-service", "event_type": "order.reconcile.scan_error"},
            )
        await asyncio.sleep(poll_seconds)
