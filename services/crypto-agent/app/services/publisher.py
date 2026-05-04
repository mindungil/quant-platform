"""
NATS publisher: publishes decision results to ``agent.crypto.action``.
"""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.models.decision import AgentState

logger = logging.getLogger(__name__)


async def publish_action(state: AgentState, nc=None) -> None:
    """
    Publish the decision result to NATS.
    ``nc`` is the nats.aio.client.Client instance (may be None if NATS is
    disabled or not connected).
    """
    if nc is None or not nc.is_connected:
        logger.debug("NATS not connected - skipping publish")
        return

    if state.decision is None:
        logger.debug("No decision to publish for %s", state.asset)
        return

    payload = {
        "id": state.decision.id,
        "agent_type": state.decision.agent_type,
        "asset": state.decision.asset,
        "signal_score": state.decision.signal_score,
        "direction": state.decision.direction,
        "action": state.decision.action,
        "strategy": state.decision.strategy,
        "memory_refs": state.decision.memory_refs,
        "reasoning": state.decision.reasoning,
        "risk_approved": state.decision.risk_approved,
        "order_id": state.decision.order_id,
        "decided_at": state.decision.decided_at.isoformat(),
    }

    # Include dual-lane tags so consumers (frontend stream, performance svc)
    # can group by lane.
    payload["lane"] = getattr(state, "lane", "agent_core")
    payload["subscription_id"] = getattr(state, "subscription_id", None)
    payload["lane_budget_pct"] = getattr(state, "lane_budget_pct", 1.0)

    subject = settings.nats_publish_subject
    try:
        data = json.dumps(payload).encode("utf-8")
        await nc.publish(subject, data)
        logger.info("Published action[%s] to '%s' for %s",
                    payload["lane"], subject, state.asset)
    except Exception:
        logger.exception("Failed to publish to '%s'", subject)


async def publish_lane_event(event_type: str, payload: dict) -> None:
    """Publish a lane.* collision/risk event so frontend and risk-service
    can consume. Event subjects: lane.signal_collision | lane.opposite_collision
    | lane.risk_rejection. Uses a standalone NATS connection (best-effort)."""
    try:
        from nats.aio.client import Client as NATS
    except Exception:
        return
    try:
        nc = NATS()
        await nc.connect(settings.nats_url)
        try:
            subject = f"agent.crypto.{event_type}"
            data = json.dumps({"event": event_type, **payload}).encode("utf-8")
            await nc.publish(subject, data)
        finally:
            await nc.drain()
        logger.info("Published lane event %s payload=%s", event_type, payload)
    except Exception:
        logger.exception("Failed to publish lane event %s", event_type)
