import random
from datetime import UTC, datetime

from app.core.market_hours import is_market_open
from app.models.agent import DecisionRecord


def run_decision_loop(asset: str, *, correlation_id: str | None = None) -> DecisionRecord:
    """Execute a single decision cycle for the given US-market stock asset.

    If the market is closed the agent immediately returns a HOLD decision.
    When the market is open a simulated signal is produced (to be replaced
    with real signal-service integration later).
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

    # --- simulated signal (placeholder) ---
    signal_score = round(random.uniform(-1.0, 1.0), 4)
    components = {
        "momentum": round(random.uniform(-1, 1), 4),
        "mean_reversion": round(random.uniform(-1, 1), 4),
        "volume": round(random.uniform(-1, 1), 4),
    }
    threshold = 0.4
    threshold_crossed = abs(signal_score) >= threshold

    if not threshold_crossed:
        action = "HOLD"
    elif signal_score > 0:
        action = "BUY"
    else:
        action = "SELL"

    reasoning = (
        f"{asset} simulated signal_score={signal_score:.4f} "
        f"({'above' if threshold_crossed else 'below'} threshold {threshold}). "
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
        market_open=True,
        correlation_id=correlation_id,
    )
    if decision.correlation_id is None:
        decision.correlation_id = decision.decision_id

    return decision
