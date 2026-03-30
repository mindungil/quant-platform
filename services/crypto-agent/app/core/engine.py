from datetime import UTC, datetime

from app.db.repository import decision_repository
from app.models.agent import DecisionRecord, MemorySearchRequest
from app.services.llm_gateway_client import LlmGatewayClient
from app.services.memory_client import MemoryClient
from app.services.signal_client import SignalClient
from app.services.strategy_client import StrategyClient
from app.core.config import settings

signal_client = SignalClient(settings.signal_service_base_url)
memory_client = MemoryClient(settings.memory_service_base_url)
strategy_client = StrategyClient(settings.strategy_registry_base_url)
llm_gateway_client = LlmGatewayClient(settings.llm_gateway_base_url)


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


def run_decision_loop(asset: str) -> DecisionRecord:
    signal = signal_client.get_latest_signal(asset)
    strategy = strategy_client.get_active_strategy("crypto")
    memory_response = memory_client.search(
        MemorySearchRequest(
            asset=asset,
            signal_score=signal.signal_score,
            action="BUY" if signal.signal_score >= 0 else "SELL",
            strategy_id=strategy.id,
        )
    )

    action = "BUY" if signal.signal_score >= signal.threshold else "SELL"
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
    )

    decision_repository.save(asset, decision)
    memory_client.record(decision.to_memory_record())
    return decision
