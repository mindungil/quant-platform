"""
Background async task that polls active positions every 30 seconds,
checking stop-loss and trailing-stop conditions.
"""

import asyncio
import json

import httpx

from app.core.config import settings
from app.core.protection import protection_manager
from app.models.order import ProtectiveOrder
from app.services.event_publisher import publisher
from shared.logging import get_logger
from shared.persistence import RedisStore

logger = get_logger("order-service")

POLL_INTERVAL_SECONDS = 30
TRAILING_KEY_PREFIX = "trailing:"

_redis = RedisStore(settings.redis_url)
_running = False
_task: asyncio.Task | None = None


def _get_trailing_state(order_id: str) -> dict | None:
    raw = _redis.get(f"{TRAILING_KEY_PREFIX}{order_id}")
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def _set_trailing_state(order_id: str, state: dict) -> None:
    _redis.set(f"{TRAILING_KEY_PREFIX}{order_id}", json.dumps(state))


def _delete_trailing_state(order_id: str) -> None:
    _redis.delete(f"{TRAILING_KEY_PREFIX}{order_id}")


def _fetch_current_price(asset: str) -> float | None:
    # order-service.config doesn't declare market_data_base_url, so we hit
    # localhost:8001 (= self) every call → ConnectError → no protection
    # triggers. Read the env directly (compose sets MARKET_DATA_BASE_URL).
    import os as _os
    market_data_url = (
        _os.environ.get("MARKET_DATA_BASE_URL")
        or getattr(settings, "market_data_base_url", None)
        or "http://market-pipeline:8001"
    )
    try:
        resp = httpx.get(f"{market_data_url}/candles/{asset}/latest", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get("close") or data.get("price", 0))
    except Exception as exc:
        logger.warning("position_monitor_price_fetch_failed",
                       extra={"asset": asset, "error": str(exc)[:120]})
    return None


def _trigger_stop(protection: ProtectiveOrder, current_price: float, trigger_reason: str, shadow_mode: bool) -> None:
    """Cancel the protective order and publish stop event."""
    if shadow_mode:
        logger.info(
            "shadow_stop_loss_triggered",
            extra={
                "service": "order-service",
                "order_id": protection.order_id,
                "asset": protection.asset,
                "trigger_type": protection.trigger_type,
                "trigger_reason": trigger_reason,
                "current_price": current_price,
                "trigger_price": protection.trigger_price,
                "shadow_mode": True,
            },
        )
        return

    # Mark protection as triggered
    protection.status = "TRIGGERED"

    # Publish NATS event
    event_type = "order.stop_loss_triggered" if "TRAILING" not in protection.trigger_type else "order.trailing_stop_triggered"
    try:
        publisher.publish_order_cancelled(protection.order_id, protection.user_id)
    except Exception:
        logger.exception("stop_loss_cancel_publish_failed", extra={"order_id": protection.order_id})

    logger.info(
        "stop_loss_triggered",
        extra={
            "service": "order-service",
            "order_id": protection.order_id,
            "asset": protection.asset,
            "trigger_type": protection.trigger_type,
            "trigger_reason": trigger_reason,
            "current_price": current_price,
            "trigger_price": protection.trigger_price,
            "event_type": event_type,
        },
    )


def _check_protection(protection: ProtectiveOrder, current_price: float, shadow_mode: bool) -> bool:
    """Check a single protective order. Returns True if triggered."""
    fill_price = protection.trigger_price  # trigger_price is set relative to fill

    if protection.trigger_type == "STOP_LOSS":
        # trigger_price is already the absolute stop price
        # protection.side is the protective order side (opposite of position)
        # SELL protective = long position, BUY protective = short position
        if protection.side == "BUY":
            # Short position: stop if price rises above trigger
            if current_price >= protection.trigger_price:
                _trigger_stop(protection, current_price, "stop_loss_hit_short", shadow_mode)
                return True
        else:
            # Long position: stop if price drops below trigger
            if current_price <= protection.trigger_price:
                _trigger_stop(protection, current_price, "stop_loss_hit", shadow_mode)
                return True

    elif protection.trigger_type == "TRAILING_STOP":
        trailing_pct = protection.trailing_stop_pct or 0.03
        state = _get_trailing_state(protection.order_id)
        is_short = protection.side == "BUY"  # BUY protective = short position

        if state is None:
            ref_price = protection.highest_price or current_price
            state = {
                "highest_price": max(ref_price, current_price),
                "lowest_price": min(ref_price, current_price),
                "fill_price": ref_price,
                "stop_pct": trailing_pct,
            }
            _set_trailing_state(protection.order_id, state)

        if is_short:
            # Short position: track lowest price, trigger on rise
            lowest = state.get("lowest_price", current_price)
            if current_price < lowest:
                state["lowest_price"] = current_price
                _set_trailing_state(protection.order_id, state)
                lowest = current_price
            if lowest > 0:
                rise_pct = (current_price - lowest) / lowest
                if rise_pct >= trailing_pct:
                    _trigger_stop(protection, current_price, f"trailing_stop_hit_short (rise={rise_pct:.4f} >= {trailing_pct})", shadow_mode)
                    _delete_trailing_state(protection.order_id)
                    return True
        else:
            # Long position: track highest price, trigger on drop
            highest = state["highest_price"]
            if current_price > highest:
                state["highest_price"] = current_price
                _set_trailing_state(protection.order_id, state)
                highest = current_price
            if highest > 0:
                drop_pct = (highest - current_price) / highest
                if drop_pct >= trailing_pct:
                    _trigger_stop(protection, current_price, f"trailing_stop_hit (drop={drop_pct:.4f} >= {trailing_pct})", shadow_mode)
                    _delete_trailing_state(protection.order_id)
                    return True

    return False


async def _monitor_loop() -> None:
    """Main monitoring loop — polls every POLL_INTERVAL_SECONDS."""
    global _running
    logger.info("position_monitor_started", extra={"service": "order-service", "interval": POLL_INTERVAL_SECONDS})

    while _running:
        try:
            # Get all active protective orders from protection_manager
            all_protections = protection_manager.get_all_active()

            # Group by asset for efficient price fetching
            assets = {p.asset for p in all_protections}
            prices: dict[str, float] = {}
            for asset in assets:
                price = _fetch_current_price(asset)
                if price is not None:
                    prices[asset] = price

            for protection in all_protections:
                if protection.status != "ACTIVE":
                    continue
                current_price = prices.get(protection.asset)
                if current_price is None:
                    continue

                # Determine shadow mode from order repository
                # For simplicity, check if the order was shadow_mode
                shadow_mode = False
                try:
                    from app.db.repository import order_repository
                    order = order_repository.get_by_id(protection.order_id)
                    if order is not None:
                        shadow_mode = order.shadow_mode
                except Exception:
                    pass

                _check_protection(protection, current_price, shadow_mode)

        except Exception:
            logger.exception("position_monitor_error", extra={"service": "order-service"})

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def start() -> None:
    """Start the position monitor background task."""
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_monitor_loop())
    logger.info("position_monitor_scheduled", extra={"service": "order-service"})


async def stop() -> None:
    """Stop the position monitor background task."""
    global _running, _task
    _running = False
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("position_monitor_stopped", extra={"service": "order-service"})
