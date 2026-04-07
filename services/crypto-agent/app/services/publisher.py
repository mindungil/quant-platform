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

    subject = settings.nats_publish_subject
    try:
        data = json.dumps(payload).encode("utf-8")
        await nc.publish(subject, data)
        logger.info("Published action to '%s' for %s", subject, state.asset)
    except Exception:
        logger.exception("Failed to publish to '%s'", subject)
