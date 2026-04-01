from __future__ import annotations

import threading

from app.models.order import OrderRequest, OrderResponse, ProtectiveOrder
from shared.logging import get_logger

logger = get_logger("order-service")


class ProtectionManager:
    """Manages stop-loss, take-profit, and trailing-stop protective orders."""

    def __init__(self) -> None:
        self._active_orders: dict[str, list[ProtectiveOrder]] = {}  # order_id -> protections
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_protections(
        self, parent_order: OrderResponse, request: OrderRequest
    ) -> list[ProtectiveOrder]:
        """Create protective orders based on the parent order's fill and request params."""
        protections: list[ProtectiveOrder] = []

        if parent_order.fill is None:
            return protections

        entry_price = parent_order.fill.filled_price
        quantity = parent_order.fill.filled_quantity
        opposite_side = "SELL" if request.side.upper() == "BUY" else "BUY"

        # Stop-loss
        if request.stop_loss_pct is not None and request.stop_loss_pct > 0:
            if request.side.upper() == "BUY":
                trigger = entry_price * (1 - request.stop_loss_pct)
            else:
                trigger = entry_price * (1 + request.stop_loss_pct)
            protections.append(
                ProtectiveOrder(
                    order_id=parent_order.order_id,
                    user_id=parent_order.user_id,
                    asset=parent_order.asset,
                    side=opposite_side,
                    trigger_type="STOP_LOSS",
                    trigger_price=round(trigger, 8),
                    quantity=quantity,
                )
            )

        # Take-profit
        if request.take_profit_pct is not None and request.take_profit_pct > 0:
            if request.side.upper() == "BUY":
                trigger = entry_price * (1 + request.take_profit_pct)
            else:
                trigger = entry_price * (1 - request.take_profit_pct)
            protections.append(
                ProtectiveOrder(
                    order_id=parent_order.order_id,
                    user_id=parent_order.user_id,
                    asset=parent_order.asset,
                    side=opposite_side,
                    trigger_type="TAKE_PROFIT",
                    trigger_price=round(trigger, 8),
                    quantity=quantity,
                )
            )

        # Trailing stop
        if request.trailing_stop_pct is not None and request.trailing_stop_pct > 0:
            if request.side.upper() == "BUY":
                trigger = entry_price * (1 - request.trailing_stop_pct)
                highest = entry_price
            else:
                trigger = entry_price * (1 + request.trailing_stop_pct)
                highest = entry_price
            protections.append(
                ProtectiveOrder(
                    order_id=parent_order.order_id,
                    user_id=parent_order.user_id,
                    asset=parent_order.asset,
                    side=opposite_side,
                    trigger_type="TRAILING_STOP",
                    trigger_price=round(trigger, 8),
                    quantity=quantity,
                    highest_price=highest,
                    trailing_stop_pct=request.trailing_stop_pct,
                )
            )

        if protections:
            with self._lock:
                self._active_orders.setdefault(parent_order.order_id, []).extend(protections)
            logger.info(
                "protective_orders_created",
                extra={
                    "service": "order-service",
                    "order_id": parent_order.order_id,
                    "count": len(protections),
                    "types": [p.trigger_type for p in protections],
                },
            )

        return protections

    # ------------------------------------------------------------------
    # Trigger checking
    # ------------------------------------------------------------------

    def check_triggers(self, asset: str, current_price: float) -> list[ProtectiveOrder]:
        """Check all active protections for an asset. Returns triggered ones."""
        triggered: list[ProtectiveOrder] = []

        with self._lock:
            for order_id, protections in list(self._active_orders.items()):
                for p in protections:
                    if p.asset != asset or p.status != "ACTIVE":
                        continue

                    # Update trailing stop highest price and recalculate trigger
                    if p.trigger_type == "TRAILING_STOP" and p.trailing_stop_pct:
                        if p.side == "SELL":
                            # Parent was BUY: track highest price, trigger below it
                            if current_price > (p.highest_price or 0):
                                p.highest_price = current_price
                                p.trigger_price = round(
                                    current_price * (1 - p.trailing_stop_pct), 8
                                )
                        else:
                            # Parent was SELL: track lowest price, trigger above it
                            if p.highest_price is None or current_price < p.highest_price:
                                p.highest_price = current_price
                                p.trigger_price = round(
                                    current_price * (1 + p.trailing_stop_pct), 8
                                )

                    # Check if trigger condition is met
                    if p.side == "SELL" and current_price <= p.trigger_price:
                        p.status = "TRIGGERED"
                        triggered.append(p)
                    elif p.side == "BUY" and current_price >= p.trigger_price:
                        p.status = "TRIGGERED"
                        triggered.append(p)

                # Cancel remaining protections for this order if any triggered
                triggered_order_ids = {t.order_id for t in triggered}
                if order_id in triggered_order_ids:
                    for p in protections:
                        if p.status == "ACTIVE":
                            p.status = "CANCELLED"

        return triggered

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_protections(self, order_id: str) -> list[ProtectiveOrder]:
        """Return all protections (any status) for a given parent order."""
        with self._lock:
            return list(self._active_orders.get(order_id, []))

    def cancel_protections(self, order_id: str) -> list[ProtectiveOrder]:
        """Cancel all active protections for a given parent order."""
        cancelled: list[ProtectiveOrder] = []
        with self._lock:
            for p in self._active_orders.get(order_id, []):
                if p.status == "ACTIVE":
                    p.status = "CANCELLED"
                    cancelled.append(p)
        return cancelled


protection_manager = ProtectionManager()
