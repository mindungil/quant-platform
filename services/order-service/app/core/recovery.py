"""Order state recovery on startup.

Finds orders stuck in non-terminal states (PENDING, APPROVED, SUBMITTED)
and resolves them so the system starts clean.
"""

from app.db.repository import order_repository
from shared.logging import get_logger

logger = get_logger("order-service")

TERMINAL_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "FAILED"}


def recover_stuck_orders(max_age_seconds: int = 300) -> int:
    """Find and resolve orders stuck in non-terminal states.

    Returns the number of recovered orders.
    """
    stuck = order_repository.find_stuck_orders(max_age_seconds=max_age_seconds)
    recovered = 0
    for order in stuck:
        if order.status in TERMINAL_STATUSES:
            continue
        try:
            if order.status == "SUBMITTED":
                # Order was submitted to exchange but we never got a response.
                # Mark as FAILED -- operator can reconcile via exchange API later.
                order_repository.update_status(
                    order.order_id,
                    "FAILED",
                    detail="recovered_on_startup:submitted_no_response",
                )
                logger.warning(
                    "order_recovered_submitted",
                    extra={
                        "service": "order-service",
                        "order_id": order.order_id,
                        "user_id": order.user_id,
                        "previous_status": order.status,
                        "event_type": "order.recovery",
                    },
                )
            else:
                # PENDING or APPROVED -- never reached exchange
                order_repository.update_status(
                    order.order_id,
                    "FAILED",
                    detail=f"recovered_on_startup:{order.status.lower()}_stale",
                )
                logger.warning(
                    "order_recovered_stale",
                    extra={
                        "service": "order-service",
                        "order_id": order.order_id,
                        "user_id": order.user_id,
                        "previous_status": order.status,
                        "event_type": "order.recovery",
                    },
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
    if recovered:
        logger.info(
            "order_recovery_complete",
            extra={
                "service": "order-service",
                "recovered_count": recovered,
                "event_type": "order.recovery.complete",
            },
        )
    return recovered
