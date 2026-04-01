from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.db.repository import decision_repository
from app.models.agent import DecisionRecord, MemorySearchRequest, PhaseResult, SignalSnapshot, StrategySnapshot, MemorySearchResponse
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIGNAL_STALENESS_SECONDS = 300  # 5 minutes
DUPLICATE_WINDOW_SECONDS = 60  # 1 minute


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class RiskPreCheckError(Exception):
    """Raised when a pre-flight risk check fails."""


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _track_phase(name: str) -> PhaseResult:
    """Create a new phase tracker and mark it as started."""
    return PhaseResult(name=name, status="started", started_at=datetime.now(UTC))


def _complete_phase(phase: PhaseResult, *, detail: str | None = None) -> PhaseResult:
    now = datetime.now(UTC)
    phase.status = "completed"
    phase.ended_at = now
    if phase.started_at:
        phase.duration_ms = round((now - phase.started_at).total_seconds() * 1000, 2)
    phase.detail = detail
    return phase


def _fail_phase(phase: PhaseResult, *, detail: str | None = None) -> PhaseResult:
    now = datetime.now(UTC)
    phase.status = "failed"
    phase.ended_at = now
    if phase.started_at:
        phase.duration_ms = round((now - phase.started_at).total_seconds() * 1000, 2)
    phase.detail = detail
    return phase


# ---------------------------------------------------------------------------
# Helpers (unchanged logic)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Risk pre-check
# ---------------------------------------------------------------------------

def _risk_pre_check(
    strategy: StrategySnapshot,
    signal: SignalSnapshot,
    asset: str,
    action: str,
) -> list[str]:
    """Run lightweight local risk validations. Returns list of warning/failure messages."""
    issues: list[str] = []

    # 1. Strategy must be ACTIVE
    if getattr(strategy, "status", "ACTIVE") != "ACTIVE":
        issues.append(f"strategy status is '{strategy.status}', expected ACTIVE")

    # 2. Signal freshness — feature_timestamp within SIGNAL_STALENESS_SECONDS
    now = datetime.now(UTC)
    feature_ts = signal.feature_timestamp
    if feature_ts.tzinfo is None:
        # treat naive as UTC
        feature_ts = feature_ts.replace(tzinfo=UTC)
    staleness = (now - feature_ts).total_seconds()
    if staleness > SIGNAL_STALENESS_SECONDS:
        issues.append(f"signal is stale ({staleness:.0f}s old, limit {SIGNAL_STALENESS_SECONDS}s)")

    # 3. Duplicate decision guard — same asset+action within DUPLICATE_WINDOW_SECONDS
    try:
        latest = decision_repository.get_latest(asset)
        if latest is not None:
            latest_ts = latest.timestamp
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=UTC)
            elapsed = (now - latest_ts).total_seconds()
            if elapsed < DUPLICATE_WINDOW_SECONDS and latest.action == action:
                issues.append(
                    f"duplicate decision ({asset} {action}) within {DUPLICATE_WINDOW_SECONDS}s window "
                    f"(last decision {elapsed:.1f}s ago)"
                )
    except Exception as exc:
        # non-fatal — log but don't block
        logger.warning("duplicate_check_failed", extra={"error": str(exc)})

    return issues


# ---------------------------------------------------------------------------
# 6-phase decision loop
# ---------------------------------------------------------------------------

def _phase_gather(
    asset: str, user_id: str | None, phases: list[PhaseResult]
) -> SignalSnapshot:
    """Phase 1 — Gather: fetch latest signal from signal-service."""
    phase = _track_phase("gather")
    signal = signal_client.get_latest_signal(asset, user_id=user_id)
    _complete_phase(phase, detail=f"signal_score={signal.signal_score:.4f}")
    phases.append(phase)
    return signal


def _phase_retrieve(
    effective_user_id: str,
    asset: str,
    signal: SignalSnapshot,
    strategy: StrategySnapshot,
    phases: list[PhaseResult],
) -> MemorySearchResponse:
    """Phase 2 — Retrieve: search memory for similar past decisions."""
    phase = _track_phase("retrieve")
    memory_response = memory_client.search(
        MemorySearchRequest(
            user_id=effective_user_id,
            asset=asset,
            signal_score=signal.signal_score,
            action=signal.direction,
            strategy_id=strategy.id,
        )
    )
    _complete_phase(phase, detail=f"matched={len(memory_response.items)}")
    phases.append(phase)
    return memory_response


def _phase_select(
    asset: str,
    signal: SignalSnapshot,
    user_id: str | None,
    phases: list[PhaseResult],
) -> tuple[StrategySnapshot, str, str]:
    """Phase 3 — Select: load active strategy and determine effective user & action."""
    phase = _track_phase("select")
    strategy = strategy_client.get_active_strategy(
        "crypto",
        user_id=user_id or getattr(signal, "strategy_user_id", None),
    )
    strategy_user_id = getattr(signal, "strategy_user_id", None) or strategy.user_id
    effective_user_id = user_id or strategy_user_id or "bootstrap"
    action = signal.direction
    _complete_phase(phase, detail=f"strategy={strategy.name} action={action}")
    phases.append(phase)
    return strategy, effective_user_id, action


def _phase_check(
    strategy: StrategySnapshot,
    signal: SignalSnapshot,
    asset: str,
    action: str,
    phases: list[PhaseResult],
) -> None:
    """Phase 4 — Check: pre-flight risk validation before execution."""
    phase = _track_phase("check")
    issues = _risk_pre_check(strategy, signal, asset, action)
    if issues:
        detail = "; ".join(issues)
        logger.warning(
            "risk_pre_check_warnings",
            extra={"asset": asset, "issues": issues},
        )
        _complete_phase(phase, detail=f"warnings: {detail}")
    else:
        _complete_phase(phase, detail="all checks passed")
    phases.append(phase)


def _phase_execute(
    asset: str,
    signal: SignalSnapshot,
    strategy: StrategySnapshot,
    memory_response: MemorySearchResponse,
    action: str,
    effective_user_id: str,
    correlation_id: str | None,
    phases: list[PhaseResult],
) -> DecisionRecord:
    """Phase 5 — Execute: generate reasoning, build decision, publish action event."""
    phase = _track_phase("execute")

    # LLM reasoning with fallback
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

    # Publish execution action if threshold crossed
    if decision.threshold_crossed and decision.action in {"BUY", "SELL"}:
        publisher.publish_agent_action(decision, _build_order_request(decision))

    _complete_phase(phase, detail=f"reasoning_len={len(reasoning)}")
    phases.append(phase)
    return decision


def _phase_record(
    asset: str,
    decision: DecisionRecord,
    phases: list[PhaseResult],
) -> None:
    """Phase 6 — Record: persist decision to DB, record to memory, publish realtime event."""
    phase = _track_phase("record")

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

    _complete_phase(phase, detail="persisted")
    phases.append(phase)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_decision_loop(asset: str, *, user_id: str | None = None, correlation_id: str | None = None) -> DecisionRecord:
    """Execute the full 6-phase decision loop: gather -> select -> retrieve -> check -> execute -> record."""
    phases: list[PhaseResult] = []

    # Phase 1 — Gather
    signal = _phase_gather(asset, user_id, phases)

    # Phase 3 — Select (run before retrieve so we have strategy for memory query)
    strategy, effective_user_id, action = _phase_select(asset, signal, user_id, phases)

    # Phase 2 — Retrieve
    memory_response = _phase_retrieve(effective_user_id, asset, signal, strategy, phases)

    # Phase 4 — Check
    _phase_check(strategy, signal, asset, action, phases)

    # Phase 5 — Execute
    decision = _phase_execute(
        asset, signal, strategy, memory_response, action,
        effective_user_id, correlation_id, phases,
    )

    # Attach phase tracking to decision before recording
    decision.decision_phases = phases

    # Phase 6 — Record
    _phase_record(asset, decision, phases)

    return decision
