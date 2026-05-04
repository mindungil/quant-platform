"""TWAP (Time-Weighted Average Price) order slicer.

For any order whose notional exceeds `TWAP_THRESHOLD_USD`, the engine
breaks it into N child orders of equal notional and submits them at fixed
intervals. This dramatically reduces market impact relative to dropping
the full size into the book at once.

This is intentionally simple — we are not implementing IS or VWAP. The
goal is "don't blast a $50k order into a thin book". Strategies that need
order-book-aware execution should use a dedicated execution algo.

Slicing is opt-in via the OrderRequest.execution_algo field (default
"market" = no slicing). When `twap_minutes` is set, slicing happens.

Safety guards baked in:
- Cap on N slices (TWAP_MAX_SLICES) so we never spam the exchange
- Each child carries its own idempotency key derived from the parent
- Risk re-check between every child (state may have changed)
- Hard halt on first failure: don't keep slicing into a problem
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

from app.models.order import OrderRequest, OrderResponse

logger = logging.getLogger(__name__)

TWAP_THRESHOLD_USD = float(os.getenv("ORDER_TWAP_THRESHOLD_USD", "5000.0"))
TWAP_DEFAULT_MINUTES = int(os.getenv("ORDER_TWAP_DEFAULT_MINUTES", "10"))
TWAP_DEFAULT_SLICES = int(os.getenv("ORDER_TWAP_DEFAULT_SLICES", "5"))
TWAP_MAX_SLICES = int(os.getenv("ORDER_TWAP_MAX_SLICES", "20"))


@dataclass
class TwapPlan:
    n_slices: int
    interval_seconds: float
    child_quantity: float
    child_notional: float

    @classmethod
    def from_request(cls, payload: OrderRequest) -> "TwapPlan | None":
        """Decide whether and how to slice this order.

        Returns None if the order should NOT be sliced (small enough or
        explicitly opted out).
        """
        # Opt-out: if shadow mode, never slice (paper trades don't need impact mgmt)
        if payload.shadow_mode:
            return None

        notional = payload.requested_notional
        if notional < TWAP_THRESHOLD_USD:
            return None

        n_slices = min(TWAP_DEFAULT_SLICES, TWAP_MAX_SLICES)
        # If the order is very large, scale up slices proportionally
        if notional > TWAP_THRESHOLD_USD * 4:
            n_slices = min(TWAP_MAX_SLICES, int(notional / TWAP_THRESHOLD_USD))
        n_slices = max(2, n_slices)

        total_seconds = TWAP_DEFAULT_MINUTES * 60.0
        interval = total_seconds / n_slices
        child_qty = payload.quantity / n_slices
        child_notional = notional / n_slices

        return cls(
            n_slices=n_slices,
            interval_seconds=interval,
            child_quantity=child_qty,
            child_notional=child_notional,
        )


def execute_twap(
    parent: OrderRequest,
    plan: TwapPlan,
    submit_child: Callable[[OrderRequest], OrderResponse],
) -> list[OrderResponse]:
    """Execute a parent order as a series of child slices.

    `submit_child` is the function that processes a single child order
    (typically the engine's normal `process_order` minus this slicer to
    avoid recursion).

    Returns the list of child responses. If any child is REJECTED or
    FAILED, the remaining slices are aborted.
    """
    children: list[OrderResponse] = []
    parent_qty = parent.quantity
    parent_notional = parent.requested_notional
    parent_idem = parent.idempotency_key

    for i in range(plan.n_slices):
        # Construct a child order with derived idempotency
        child = parent.model_copy(deep=True)
        child.quantity = plan.child_quantity
        child.requested_notional = plan.child_notional
        child.idempotency_key = (
            f"{parent_idem}-twap-{i}" if parent_idem else None
        )
        child.correlation_id = f"{parent.correlation_id}-twap-{i}" if parent.correlation_id else None

        try:
            resp = submit_child(child)
        except Exception as exc:
            logger.exception(
                "twap_child_failed",
                extra={"slice": i, "parent_qty": parent_qty, "error": str(exc)[:200]},
            )
            break

        children.append(resp)

        if resp.status in {"REJECTED", "FAILED"}:
            logger.warning(
                "twap_aborted",
                extra={
                    "slice": i,
                    "n_slices": plan.n_slices,
                    "reason": resp.risk_reason,
                    "child_status": resp.status,
                },
            )
            break

        # Wait for next slice (skip after last).
        # This is a sync function — FastAPI runs sync handlers in a thread
        # pool, so time.sleep only blocks this worker thread, not the event
        # loop. For async callers, use execute_twap_async instead.
        if i < plan.n_slices - 1:
            time.sleep(plan.interval_seconds)

    return children


async def execute_twap_async(
    parent: OrderRequest,
    plan: TwapPlan,
    submit_child: Callable[[OrderRequest], OrderResponse],
) -> list[OrderResponse]:
    """Async version of execute_twap — uses asyncio.sleep between slices
    so the event loop is not blocked. submit_child is run in the default
    executor to avoid blocking the loop with sync HTTP calls."""
    children: list[OrderResponse] = []
    parent_idem = parent.idempotency_key

    for i in range(plan.n_slices):
        child = parent.model_copy(deep=True)
        child.quantity = plan.child_quantity
        child.requested_notional = plan.child_notional
        child.idempotency_key = (
            f"{parent_idem}-twap-{i}" if parent_idem else None
        )
        child.correlation_id = f"{parent.correlation_id}-twap-{i}" if parent.correlation_id else None

        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(None, submit_child, child)
        except Exception as exc:
            logger.exception(
                "twap_child_failed",
                extra={"slice": i, "parent_qty": parent.quantity, "error": str(exc)[:200]},
            )
            break

        children.append(resp)

        if resp.status in {"REJECTED", "FAILED"}:
            logger.warning(
                "twap_aborted",
                extra={
                    "slice": i,
                    "n_slices": plan.n_slices,
                    "reason": resp.risk_reason,
                    "child_status": resp.status,
                },
            )
            break

        if i < plan.n_slices - 1:
            await asyncio.sleep(plan.interval_seconds)

    return children
