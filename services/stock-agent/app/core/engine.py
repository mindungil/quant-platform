import logging
import os
from datetime import UTC, datetime

import httpx

from app.core.market_hours import is_market_open
from app.models.agent import DecisionRecord

logger = logging.getLogger("stock-agent")

SIGNAL_SERVICE_URL = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
THRESHOLD = 0.4


def _fetch_signal(asset: str) -> dict:
    """Fetch the latest signal for *asset* from signal-service."""
    url = f"{SIGNAL_SERVICE_URL}/signals/{asset}/latest"
    resp = httpx.get(url, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def run_decision_loop(asset: str, *, correlation_id: str | None = None) -> DecisionRecord:
    """Execute a single decision cycle for the given US-market stock asset.

    If the market is closed the agent immediately returns a HOLD decision.
    When the market is open the signal-service is queried for the real signal.
    """
    now = datetime.now()
    market_open = is_market_open(now)

    if not market_open:
        return DecisionRecord(
            timestamp=datetime.now(UTC),
            asset=asset,
            asset_type="stock",
            action="HOLD",
            signal_score=0.0,
            threshold_crossed=False,
            reasoning="market_closed",
            market_open=False,
            correlation_id=correlation_id,
        )

    # --- real signal-service integration ---
    try:
        data = _fetch_signal(asset)
    except Exception as exc:
        logger.warning("signal_fetch_failed", extra={"asset": asset, "error": str(exc)[:200]})
        return DecisionRecord(
            timestamp=datetime.now(UTC),
            asset=asset,
            asset_type="stock",
            action="HOLD",
            signal_score=0.0,
            threshold_crossed=False,
            reasoning=f"signal_fetch_failed: {exc}",
            market_open=True,
            correlation_id=correlation_id,
        )

    signal_score = float(data.get("signal_score", 0.0))
    direction = data.get("direction", "HOLD")
    components = data.get("components", {})
    reference_price = data.get("reference_price")

    threshold_crossed = abs(signal_score) >= THRESHOLD

    if not threshold_crossed:
        action = "HOLD"
    else:
        action = direction if direction in ("BUY", "SELL") else ("BUY" if signal_score > 0 else "SELL")

    reasoning = (
        f"{asset} signal_score={signal_score:.4f} direction={direction} "
        f"({'above' if threshold_crossed else 'below'} threshold {THRESHOLD}). "
        f"Action: {action}."
    )

    decision = DecisionRecord(
        timestamp=datetime.now(UTC),
        asset=asset,
        asset_type="stock",
        action=action,
        signal_score=signal_score,
        threshold_crossed=threshold_crossed,
        reasoning=reasoning,
        components=components,
        reference_price=reference_price,
        market_open=True,
        correlation_id=correlation_id,
    )
    if decision.correlation_id is None:
        decision.correlation_id = decision.decision_id

    return decision
