from datetime import UTC, datetime

from app.core.config import settings
from app.db.repository import decision_repository
from app.models.agent import DecisionRecord, MemorySearchRequest
from app.services.llm_gateway_client import LlmGatewayClient
from app.services.memory_client import MemoryClient
from app.services.signal_client import SignalClient
from app.services.strategy_client import StrategyClient
from app.services.event_publisher import publisher
from shared.logging import get_logger
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus

signal_client = SignalClient(settings.signal_service_base_url)
memory_client = MemoryClient(settings.memory_service_base_url)
strategy_client = StrategyClient(settings.strategy_registry_base_url)
llm_gateway_client = LlmGatewayClient(settings.llm_gateway_base_url)
realtime_bus = RealtimeBus(RedisStore(settings.redis_url))
logger = get_logger("crypto-agent")


def _fallback_reasoning(
    asset: str, strategy_name: str, signal_score: float, memory_count: int, components: dict[str, float]
) -> str:
    direction = "bullish" if signal_score >= 0 else "bearish"
    strongest = ", ".join(
        f"{name}={value:.2f}"
        for name, value in sorted(components.items(), key=lambda item: abs(item[1]), reverse=True)[:3]
    )
    return (
        f"{asset} signal is {direction} with score {signal_score:.4f}. "
        f"Strategy '{strategy_name}' was selected. "
        f"Top components: {strongest or 'n/a'}. "
        f"Referenced {memory_count} similar memory items."
    )


def _build_order_request(decision: DecisionRecord) -> dict:
    reference_price = decision.reference_price or 0.0
    requested_notional = settings.default_requested_notional
    quantity = round(requested_notional / reference_price, 6) if reference_price > 0 else 0.01
    return {
        "user_id": decision.user_id,
        "exchange": settings.default_exchange,
        "asset": decision.asset,
        "side": decision.action,
        "quantity": quantity,
        "price": reference_price,
        "requested_notional": requested_notional,
        "max_notional": settings.default_max_notional,
        "current_drawdown": settings.default_current_drawdown,
        "current_exposure": settings.default_current_exposure,
        "exposure_limit": settings.default_exposure_limit,
        "automation_enabled": settings.default_automation_enabled,
        "shadow_mode": True,
        "strategy_id": decision.strategy_id,
        "strategy_status": "ACTIVE",
        "correlation_id": decision.correlation_id,
    }


def run_decision_loop(asset: str, *, user_id: str | None = None, correlation_id: str | None = None) -> DecisionRecord:
    signal = signal_client.get_latest_signal(asset, user_id=user_id)
    strategy = strategy_client.get_active_strategy("crypto", user_id=user_id or getattr(signal, "strategy_user_id", None))
    strategy_user_id = getattr(signal, "strategy_user_id", None) or strategy.user_id
    effective_user_id = user_id or strategy_user_id or "bootstrap"
    memory_response = memory_client.search(
        MemorySearchRequest(
            user_id=effective_user_id,
            asset=asset,
            signal_score=signal.signal_score,
            action=signal.direction,
            strategy_id=strategy.id,
        )
    )

    action = signal.direction
    reasoning = _fallback_reasoning(
        asset,
        strategy.name,
        signal.signal_score,
        len(memory_response.items),
        signal.components,
    )
    try:
        reasoning = llm_gateway_client.generate_reasoning(
            asset=asset,
            signal_score=signal.signal_score,
            strategy_name=strategy.name,
            memory_count=len(memory_response.items),
            components=signal.components,
        )
    except Exception:
        pass

    decision = DecisionRecord(
        timestamp=datetime.now(UTC),
        user_id=effective_user_id,
        asset=asset,
        asset_type="crypto",
        signal_score=signal.signal_score,
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        action=action,
        threshold_crossed=signal.threshold_crossed,
        reasoning=reasoning,
        memory_refs=[item.record.id for item in memory_response.items],
        components=signal.components,
        correlation_id=correlation_id,
        reference_price=getattr(signal, "reference_price", None),
    )
    if decision.correlation_id is None:
        decision.correlation_id = decision.decision_id

    decision_repository.save(asset, decision)
    memory_client.record(decision.to_memory_record())
    realtime_bus.publish(
        event_type="agent.decision",
        source="crypto-agent",
        user_id=decision.user_id,
        correlation_id=decision.correlation_id,
        data={
            "decision_id": decision.decision_id,
            "asset": decision.asset,
            "asset_type": decision.asset_type,
            "action": decision.action,
            "signal_score": decision.signal_score,
            "strategy_id": decision.strategy_id,
            "strategy_name": decision.strategy_name,
            "reasoning": decision.reasoning,
            "threshold_crossed": decision.threshold_crossed,
            "timestamp": decision.timestamp.isoformat(),
            "reference_price": decision.reference_price,
        },
    )
    logger.info(
        "decision_recorded",
        extra={
            "service": "crypto-agent",
            "correlation_id": decision.correlation_id,
            "user_id": decision.user_id,
            "event_type": "agent.decision",
        },
    )
    if decision.threshold_crossed and decision.action in {"BUY", "SELL"}:
        publisher.publish_agent_action(decision, _build_order_request(decision))
    return decision
