"""Deterministic single-strategy risk gates, health checks, and reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from string import hexdigits

from .contracts import RiskDecision
from .execution_engine import EventSourcedExecutionEngine, ExecutionState
from .finance import OrderSide
from .margin_simulator import MarginAccountSnapshot

ZERO = Decimal("0")
ONE = Decimal("1")


class RiskDecisionKind(StrEnum):
    PRE_TRADE = "PRE_TRADE"
    POST_TRADE = "POST_TRADE"
    STREAM_HEALTH = "STREAM_HEALTH"
    RECONCILIATION = "RECONCILIATION"


class RiskViolationCode(StrEnum):
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    DATA_FROM_FUTURE = "DATA_FROM_FUTURE"
    STALE_DATA = "STALE_DATA"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    REDUCE_ONLY_VIOLATION = "REDUCE_ONLY_VIOLATION"
    EQUITY_NON_POSITIVE = "EQUITY_NON_POSITIVE"
    ORDER_NOTIONAL_LIMIT = "ORDER_NOTIONAL_LIMIT"
    POSITION_NOTIONAL_LIMIT = "POSITION_NOTIONAL_LIMIT"
    LEVERAGE_LIMIT = "LEVERAGE_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    EVENT_SEQUENCE_GAP = "EVENT_SEQUENCE_GAP"
    EVENT_CLOCK_REGRESSION = "EVENT_CLOCK_REGRESSION"
    DUPLICATE_EVENT_ID = "DUPLICATE_EVENT_ID"
    REPLAY_DIVERGENCE = "REPLAY_DIVERGENCE"
    CHECKPOINT_SEQUENCE_MISMATCH = "CHECKPOINT_SEQUENCE_MISMATCH"
    CHECKPOINT_EVENT_DIGEST_MISMATCH = "CHECKPOINT_EVENT_DIGEST_MISMATCH"
    CHECKPOINT_STATE_DIGEST_MISMATCH = "CHECKPOINT_STATE_DIGEST_MISMATCH"
    CASH_INVARIANT = "CASH_INVARIANT"
    POSITION_INVARIANT = "POSITION_INVARIANT"
    MARGIN_INVARIANT = "MARGIN_INVARIANT"
    SNAPSHOT_ACCOUNT_MISMATCH = "SNAPSHOT_ACCOUNT_MISMATCH"
    SNAPSHOT_SYMBOL_MISMATCH = "SNAPSHOT_SYMBOL_MISMATCH"
    SNAPSHOT_CURRENCY_MISMATCH = "SNAPSHOT_CURRENCY_MISMATCH"
    BROKER_POSITION_MISMATCH = "BROKER_POSITION_MISMATCH"
    BROKER_CASH_MISMATCH = "BROKER_CASH_MISMATCH"
    BROKER_SEQUENCE_MISMATCH = "BROKER_SEQUENCE_MISMATCH"


class KillSwitchState(StrEnum):
    CLEAR = "CLEAR"
    ENGAGED = "ENGAGED"


class KillSwitchSource(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    RECOVERY = "RECOVERY"


@dataclass(frozen=True, slots=True)
class SingleStrategyRiskPolicy:
    policy_id: str
    schema_version: str
    symbol: str
    settlement_currency: str
    max_order_notional: Decimal
    max_position_notional: Decimal
    max_leverage: Decimal
    max_daily_loss: Decimal
    max_data_age: timedelta
    position_tolerance: Decimal = ZERO
    cash_tolerance: Decimal = ZERO
    allow_size_reduction: bool = True
    model_version: str = "single-strategy-risk-v1"

    def __post_init__(self) -> None:
        for name in (
            "policy_id",
            "schema_version",
            "symbol",
            "settlement_currency",
            "model_version",
        ):
            _text(getattr(self, name), name)
        for name in (
            "max_order_notional",
            "max_position_notional",
            "max_leverage",
            "max_daily_loss",
        ):
            _positive(getattr(self, name), name)
        for name in ("position_tolerance", "cash_tolerance"):
            _nonnegative(getattr(self, name), name)
        if self.max_data_age <= timedelta(0):
            raise ValueError("max_data_age must be positive")


@dataclass(frozen=True, slots=True)
class PreTradeRiskRequest:
    decision_id: str
    occurred_at: datetime
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    data_observed_at: datetime
    current_position_quantity: Decimal
    equity: Decimal
    daily_pnl: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        for name in ("decision_id", "account_id", "symbol"):
            _text(getattr(self, name), name)
        _aware(self.occurred_at, "occurred_at")
        _aware(self.data_observed_at, "data_observed_at")
        _positive(self.quantity, "quantity")
        _positive(self.reference_price, "reference_price")
        for name in ("current_position_quantity", "equity", "daily_pnl"):
            _finite(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class RiskViolation:
    code: RiskViolationCode
    message: str
    actual: str = ""
    limit: str = ""

    def __post_init__(self) -> None:
        _text(self.message, "message")


@dataclass(frozen=True, slots=True)
class RiskMetric:
    name: str
    value: str

    def __post_init__(self) -> None:
        _text(self.name, "name")


@dataclass(frozen=True, slots=True)
class RiskDecisionRecord:
    sequence: int
    decision_id: str
    kind: RiskDecisionKind
    occurred_at: datetime
    account_id: str
    symbol: str
    policy_id: str
    decision: RiskDecision
    violations: tuple[RiskViolation, ...] = ()
    metrics: tuple[RiskMetric, ...] = ()

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        for name in ("decision_id", "account_id", "symbol", "policy_id"):
            _text(getattr(self, name), name)
        _aware(self.occurred_at, "occurred_at")

    def to_json(self) -> str:
        return json.dumps(_decision_dict(self), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    sequence: int
    event_id: str
    occurred_at: datetime
    state: KillSwitchState
    source: KillSwitchSource
    reason: str

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        _text(self.event_id, "event_id")
        _aware(self.occurred_at, "occurred_at")
        _text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class RiskCheckpoint:
    checkpoint_id: str
    created_at: datetime
    event_sequence: int
    event_log_sha256: str
    state_sha256: str

    def __post_init__(self) -> None:
        _text(self.checkpoint_id, "checkpoint_id")
        _aware(self.created_at, "created_at")
        if self.event_sequence < 0:
            raise ValueError("event_sequence must be non-negative")
        _sha256(self.event_log_sha256, "event_log_sha256")
        _sha256(self.state_sha256, "state_sha256")

    @classmethod
    def from_engine(
        cls,
        *,
        checkpoint_id: str,
        created_at: datetime,
        engine: EventSourcedExecutionEngine,
    ) -> RiskCheckpoint:
        return cls(
            checkpoint_id=checkpoint_id,
            created_at=created_at,
            event_sequence=len(engine.events),
            event_log_sha256=_digest(engine.events_json()),
            state_sha256=_digest(engine.state.to_json()),
        )


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    snapshot_id: str
    observed_at: datetime
    account_id: str
    symbol: str
    currency: str
    position_quantity: Decimal
    cash_balance: Decimal
    latest_event_sequence: int | None = None

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "account_id", "symbol", "currency"):
            _text(getattr(self, name), name)
        _aware(self.observed_at, "observed_at")
        _finite(self.position_quantity, "position_quantity")
        _finite(self.cash_balance, "cash_balance")
        if self.latest_event_sequence is not None and self.latest_event_sequence < 0:
            raise ValueError("latest_event_sequence must be non-negative")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    snapshot_id: str
    decision: RiskDecisionRecord
    internal_position_quantity: Decimal
    external_position_quantity: Decimal
    internal_cash_balance: Decimal
    external_cash_balance: Decimal
    internal_event_sequence: int
    external_event_sequence: int | None

    @property
    def matched(self) -> bool:
        return self.decision.decision.allowed

    def to_json(self) -> str:
        payload = {
            "decision": json.loads(self.decision.to_json()),
            "external_cash_balance": str(self.external_cash_balance),
            "external_event_sequence": self.external_event_sequence,
            "external_position_quantity": str(self.external_position_quantity),
            "internal_cash_balance": str(self.internal_cash_balance),
            "internal_event_sequence": self.internal_event_sequence,
            "internal_position_quantity": str(self.internal_position_quantity),
            "matched": self.matched,
            "snapshot_id": self.snapshot_id,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class SingleStrategyRiskEngine:
    """Fail-closed risk gates and reconciliation for one strategy and symbol."""

    def __init__(self, policy: SingleStrategyRiskPolicy) -> None:
        self.policy = policy
        self._decisions: list[RiskDecisionRecord] = []
        self._kill_events: list[KillSwitchEvent] = []
        self._audit_ids: set[str] = set()
        self._audit_sequence = 0
        self._last_audit_time: datetime | None = None
        self._kill_state = KillSwitchState.CLEAR

    @property
    def decisions(self) -> tuple[RiskDecisionRecord, ...]:
        return tuple(self._decisions)

    @property
    def kill_switch_events(self) -> tuple[KillSwitchEvent, ...]:
        return tuple(self._kill_events)

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_state is KillSwitchState.ENGAGED

    def engage_kill_switch(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        reason: str,
        source: KillSwitchSource = KillSwitchSource.MANUAL,
    ) -> KillSwitchEvent:
        if self.kill_switch_engaged:
            raise ValueError("kill switch is already engaged")
        if source is KillSwitchSource.RECOVERY:
            raise ValueError("RECOVERY source is reserved for clearing the kill switch")
        event = self._new_kill_event(
            event_id=event_id,
            occurred_at=occurred_at,
            state=KillSwitchState.ENGAGED,
            source=source,
            reason=reason,
        )
        self._kill_state = KillSwitchState.ENGAGED
        self._kill_events.append(event)
        return event

    def clear_kill_switch(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        reason: str,
    ) -> KillSwitchEvent:
        if not self.kill_switch_engaged:
            raise ValueError("kill switch is already clear")
        event = self._new_kill_event(
            event_id=event_id,
            occurred_at=occurred_at,
            state=KillSwitchState.CLEAR,
            source=KillSwitchSource.RECOVERY,
            reason=reason,
        )
        self._kill_state = KillSwitchState.CLEAR
        self._kill_events.append(event)
        return event

    def pre_trade(self, request: PreTradeRiskRequest) -> RiskDecisionRecord:
        violations: list[RiskViolation] = []
        metrics: list[RiskMetric] = []
        blocking = False
        signed_requested = request.quantity if request.side is OrderSide.BUY else -request.quantity
        projected_position = request.current_position_quantity + signed_requested
        risk_reducing = (
            abs(projected_position) < abs(request.current_position_quantity)
            and request.current_position_quantity * projected_position >= ZERO
        )

        if request.symbol != self.policy.symbol:
            violations.append(_violation(
                RiskViolationCode.SYMBOL_MISMATCH,
                "request symbol does not match the risk policy",
                request.symbol,
                self.policy.symbol,
            ))
            blocking = True
        if request.reduce_only and not risk_reducing:
            violations.append(_violation(
                RiskViolationCode.REDUCE_ONLY_VIOLATION,
                "reduce-only order would not strictly reduce the current exposure",
                str(projected_position),
                f"abs(position) < {abs(request.current_position_quantity)} without reversal",
            ))
            blocking = True
        if self.kill_switch_engaged and not (request.reduce_only and risk_reducing):
            violations.append(_violation(
                RiskViolationCode.KILL_SWITCH_ENGAGED,
                "kill switch blocks new or exposure-increasing orders",
                self._kill_state.value,
                "CLEAR or valid reduce-only",
            ))
            blocking = True
        if request.data_observed_at > request.occurred_at:
            violations.append(_violation(
                RiskViolationCode.DATA_FROM_FUTURE,
                "market data timestamp is after the risk decision time",
                request.data_observed_at.isoformat(),
                request.occurred_at.isoformat(),
            ))
            blocking = True
        else:
            data_age = request.occurred_at - request.data_observed_at
            metrics.append(RiskMetric("data_age_seconds", _seconds(data_age)))
            if data_age > self.policy.max_data_age:
                violations.append(_violation(
                    RiskViolationCode.STALE_DATA,
                    "market data is older than the configured maximum",
                    _seconds(data_age),
                    _seconds(self.policy.max_data_age),
                ))
                blocking = True
        if (
            request.daily_pnl <= -self.policy.max_daily_loss
            and not (request.reduce_only and risk_reducing)
        ):
            violations.append(_violation(
                RiskViolationCode.DAILY_LOSS_LIMIT,
                "daily loss limit has been reached",
                str(request.daily_pnl),
                str(-self.policy.max_daily_loss),
            ))
            blocking = True
        if request.equity <= ZERO:
            violations.append(_violation(
                RiskViolationCode.EQUITY_NON_POSITIVE,
                "positive equity is required for risk sizing",
                str(request.equity),
                "> 0",
            ))
            blocking = True

        order_notional = request.quantity * request.reference_price
        projected_notional = abs(projected_position) * request.reference_price
        projected_leverage = projected_notional / request.equity if request.equity > ZERO else None
        metrics.extend((
            RiskMetric("requested_order_notional", str(order_notional)),
            RiskMetric("projected_position_quantity", str(projected_position)),
            RiskMetric("projected_position_notional", str(projected_notional)),
            RiskMetric("projected_leverage", "undefined" if projected_leverage is None else str(projected_leverage)),
            RiskMetric("daily_pnl", str(request.daily_pnl)),
            RiskMetric("risk_reducing", str(risk_reducing).lower()),
        ))
        if order_notional > self.policy.max_order_notional:
            violations.append(_violation(
                RiskViolationCode.ORDER_NOTIONAL_LIMIT,
                "requested order notional exceeds the configured maximum",
                str(order_notional),
                str(self.policy.max_order_notional),
            ))
        if projected_notional > self.policy.max_position_notional:
            violations.append(_violation(
                RiskViolationCode.POSITION_NOTIONAL_LIMIT,
                "projected position notional exceeds the configured maximum",
                str(projected_notional),
                str(self.policy.max_position_notional),
            ))
        if projected_leverage is not None and projected_leverage > self.policy.max_leverage:
            violations.append(_violation(
                RiskViolationCode.LEVERAGE_LIMIT,
                "projected leverage exceeds the configured maximum",
                str(projected_leverage),
                str(self.policy.max_leverage),
            ))

        allowed_quantity = request.quantity
        limit_violations = {
            RiskViolationCode.ORDER_NOTIONAL_LIMIT,
            RiskViolationCode.POSITION_NOTIONAL_LIMIT,
            RiskViolationCode.LEVERAGE_LIMIT,
        }
        if not blocking and any(item.code in limit_violations for item in violations):
            allowed_quantity = self._allowed_quantity(request)
            if not self.policy.allow_size_reduction or allowed_quantity <= ZERO:
                blocking = True

        if blocking:
            decision = RiskDecision(False, 0.0, _reason(violations))
        elif allowed_quantity < request.quantity:
            metrics.append(RiskMetric("allowed_quantity", str(allowed_quantity)))
            decision = RiskDecision(
                True,
                float(allowed_quantity / request.quantity),
                "order size reduced to satisfy risk limits",
            )
        else:
            decision = RiskDecision(True, 1.0, "allowed")
        return self._append_decision(
            decision_id=request.decision_id,
            kind=RiskDecisionKind.PRE_TRADE,
            occurred_at=request.occurred_at,
            account_id=request.account_id,
            symbol=request.symbol,
            decision=decision,
            violations=tuple(violations),
            metrics=tuple(metrics),
        )

    def assess_post_trade(
        self,
        *,
        decision_id: str,
        occurred_at: datetime,
        account_id: str,
        engine: EventSourcedExecutionEngine,
        margin_snapshot: MarginAccountSnapshot | None = None,
    ) -> RiskDecisionRecord:
        violations = self._engine_violations(engine)
        violations.extend(self._state_invariant_violations(engine.state, account_id))
        if margin_snapshot is not None:
            violations.extend(self._margin_violations(margin_snapshot, account_id))
        decision = self._decision_for_violations(
            decision_id=decision_id,
            occurred_at=occurred_at,
            violations=violations,
            success_reason="post-trade state valid",
        )
        return self._append_decision(
            decision_id=decision_id,
            kind=RiskDecisionKind.POST_TRADE,
            occurred_at=occurred_at,
            account_id=account_id,
            symbol=self.policy.symbol,
            decision=decision,
            violations=tuple(violations),
            metrics=(
                RiskMetric("event_sequence", str(len(engine.events))),
                RiskMetric("event_log_sha256", _digest(engine.events_json())),
                RiskMetric("state_sha256", _digest(engine.state.to_json())),
            ),
        )

    def inspect_stream(
        self,
        *,
        decision_id: str,
        occurred_at: datetime,
        account_id: str,
        latest_market_data_at: datetime,
        engine: EventSourcedExecutionEngine,
        checkpoint: RiskCheckpoint,
    ) -> RiskDecisionRecord:
        _aware(latest_market_data_at, "latest_market_data_at")
        violations = self._engine_violations(engine)
        if latest_market_data_at > occurred_at:
            violations.append(_violation(
                RiskViolationCode.DATA_FROM_FUTURE,
                "latest market data is after the health-check time",
                latest_market_data_at.isoformat(),
                occurred_at.isoformat(),
            ))
        elif occurred_at - latest_market_data_at > self.policy.max_data_age:
            violations.append(_violation(
                RiskViolationCode.STALE_DATA,
                "latest market data is stale",
                _seconds(occurred_at - latest_market_data_at),
                _seconds(self.policy.max_data_age),
            ))
        current_sequence = len(engine.events)
        event_digest = _digest(engine.events_json())
        state_digest = _digest(engine.state.to_json())
        if current_sequence != checkpoint.event_sequence:
            violations.append(_violation(
                RiskViolationCode.CHECKPOINT_SEQUENCE_MISMATCH,
                "event sequence does not match the expected checkpoint",
                str(current_sequence),
                str(checkpoint.event_sequence),
            ))
        if event_digest != checkpoint.event_log_sha256:
            violations.append(_violation(
                RiskViolationCode.CHECKPOINT_EVENT_DIGEST_MISMATCH,
                "event log digest does not match the expected checkpoint",
                event_digest,
                checkpoint.event_log_sha256,
            ))
        if state_digest != checkpoint.state_sha256:
            violations.append(_violation(
                RiskViolationCode.CHECKPOINT_STATE_DIGEST_MISMATCH,
                "execution state digest does not match the expected checkpoint",
                state_digest,
                checkpoint.state_sha256,
            ))
        decision = self._decision_for_violations(
            decision_id=decision_id,
            occurred_at=occurred_at,
            violations=violations,
            success_reason="stream healthy",
        )
        return self._append_decision(
            decision_id=decision_id,
            kind=RiskDecisionKind.STREAM_HEALTH,
            occurred_at=occurred_at,
            account_id=account_id,
            symbol=self.policy.symbol,
            decision=decision,
            violations=tuple(violations),
            metrics=(
                RiskMetric("checkpoint_id", checkpoint.checkpoint_id),
                RiskMetric("event_sequence", str(current_sequence)),
                RiskMetric("event_log_sha256", event_digest),
                RiskMetric("state_sha256", state_digest),
            ),
        )

    def reconcile(
        self,
        *,
        decision_id: str,
        occurred_at: datetime,
        engine: EventSourcedExecutionEngine,
        broker: BrokerSnapshot,
    ) -> ReconciliationResult:
        violations: list[RiskViolation] = []
        if broker.symbol != self.policy.symbol:
            violations.append(_violation(
                RiskViolationCode.SNAPSHOT_SYMBOL_MISMATCH,
                "broker symbol does not match the risk policy",
                broker.symbol,
                self.policy.symbol,
            ))
        if broker.currency != self.policy.settlement_currency:
            violations.append(_violation(
                RiskViolationCode.SNAPSHOT_CURRENCY_MISMATCH,
                "broker currency does not match the risk policy",
                broker.currency,
                self.policy.settlement_currency,
            ))
        internal_position = _position_quantity(engine.state, broker.account_id, self.policy.symbol)
        internal_cash = _cash_balance(engine.state, broker.account_id, self.policy.settlement_currency)
        internal_sequence = len(engine.events)
        if abs(internal_position - broker.position_quantity) > self.policy.position_tolerance:
            violations.append(_violation(
                RiskViolationCode.BROKER_POSITION_MISMATCH,
                "broker and internal position quantities differ",
                str(broker.position_quantity),
                str(internal_position),
            ))
        if abs(internal_cash - broker.cash_balance) > self.policy.cash_tolerance:
            violations.append(_violation(
                RiskViolationCode.BROKER_CASH_MISMATCH,
                "broker and internal cash balances differ",
                str(broker.cash_balance),
                str(internal_cash),
            ))
        if broker.latest_event_sequence is not None and broker.latest_event_sequence != internal_sequence:
            violations.append(_violation(
                RiskViolationCode.BROKER_SEQUENCE_MISMATCH,
                "broker and internal event sequences differ",
                str(broker.latest_event_sequence),
                str(internal_sequence),
            ))
        decision = self._decision_for_violations(
            decision_id=decision_id,
            occurred_at=occurred_at,
            violations=violations,
            success_reason="reconciled",
        )
        record = self._append_decision(
            decision_id=decision_id,
            kind=RiskDecisionKind.RECONCILIATION,
            occurred_at=occurred_at,
            account_id=broker.account_id,
            symbol=broker.symbol,
            decision=decision,
            violations=tuple(violations),
            metrics=(
                RiskMetric("snapshot_id", broker.snapshot_id),
                RiskMetric("internal_position_quantity", str(internal_position)),
                RiskMetric("external_position_quantity", str(broker.position_quantity)),
                RiskMetric("internal_cash_balance", str(internal_cash)),
                RiskMetric("external_cash_balance", str(broker.cash_balance)),
            ),
        )
        return ReconciliationResult(
            snapshot_id=broker.snapshot_id,
            decision=record,
            internal_position_quantity=internal_position,
            external_position_quantity=broker.position_quantity,
            internal_cash_balance=internal_cash,
            external_cash_balance=broker.cash_balance,
            internal_event_sequence=internal_sequence,
            external_event_sequence=broker.latest_event_sequence,
        )

    def audit_json(self) -> str:
        rows: list[tuple[int, dict[str, object]]] = []
        for decision in self._decisions:
            rows.append((decision.sequence, {"type": "risk_decision", **_decision_dict(decision)}))
        for event in self._kill_events:
            rows.append((event.sequence, {
                "type": "kill_switch",
                "sequence": event.sequence,
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "state": event.state.value,
                "source": event.source.value,
                "reason": event.reason,
            }))
        rows.sort(key=lambda item: item[0])
        return json.dumps([row for _, row in rows], sort_keys=True, separators=(",", ":"))

    def _allowed_quantity(self, request: PreTradeRiskRequest) -> Decimal:
        order_cap = self.policy.max_order_notional / request.reference_price
        position_cap = self.policy.max_position_notional / request.reference_price
        leverage_cap = request.equity * self.policy.max_leverage / request.reference_price
        direction = ONE if request.side is OrderSide.BUY else -ONE
        return max(ZERO, min(
            request.quantity,
            order_cap,
            _max_order_for_position(request.current_position_quantity, direction, position_cap),
            _max_order_for_position(request.current_position_quantity, direction, leverage_cap),
        ))

    def _decision_for_violations(
        self,
        *,
        decision_id: str,
        occurred_at: datetime,
        violations: list[RiskViolation],
        success_reason: str,
    ) -> RiskDecision:
        if not violations:
            return RiskDecision(True, 1.0, success_reason)
        self._engage_automatic(
            event_id=f"auto-{decision_id}",
            occurred_at=occurred_at,
            reason=_reason(violations),
        )
        return RiskDecision(False, 0.0, _reason(violations))

    def _engine_violations(self, engine: EventSourcedExecutionEngine) -> list[RiskViolation]:
        violations: list[RiskViolation] = []
        seen: set[str] = set()
        previous_time: datetime | None = None
        for expected, event in enumerate(engine.events, start=1):
            if event.sequence != expected:
                violations.append(_violation(
                    RiskViolationCode.EVENT_SEQUENCE_GAP,
                    "event sequence is not contiguous",
                    str(event.sequence),
                    str(expected),
                ))
            if event.event_id in seen:
                violations.append(_violation(
                    RiskViolationCode.DUPLICATE_EVENT_ID,
                    "event ID is duplicated",
                    event.event_id,
                    "unique",
                ))
            seen.add(event.event_id)
            if previous_time is not None and event.occurred_at < previous_time:
                violations.append(_violation(
                    RiskViolationCode.EVENT_CLOCK_REGRESSION,
                    "event timestamp regressed",
                    event.occurred_at.isoformat(),
                    previous_time.isoformat(),
                ))
            previous_time = event.occurred_at
        try:
            replayed = EventSourcedExecutionEngine.replay(engine.events)
        except ValueError as exc:
            violations.append(_violation(
                RiskViolationCode.REPLAY_DIVERGENCE,
                f"event replay failed: {exc}",
            ))
        else:
            if replayed.events_json() != engine.events_json() or replayed.state.to_json() != engine.state.to_json():
                violations.append(_violation(
                    RiskViolationCode.REPLAY_DIVERGENCE,
                    "event replay does not reproduce the current engine",
                ))
        return violations

    def _state_invariant_violations(self, state: ExecutionState, account_id: str) -> list[RiskViolation]:
        violations: list[RiskViolation] = []
        for balance in state.cash:
            if balance.account_id == account_id and not balance.balance.is_finite():
                violations.append(_violation(
                    RiskViolationCode.CASH_INVARIANT,
                    "cash balance must be finite",
                    str(balance.balance),
                    "finite",
                ))
        for position in state.positions:
            if position.account_id != account_id or position.symbol != self.policy.symbol:
                continue
            if (
                not position.quantity.is_finite()
                or not position.average_price.is_finite()
                or not position.realized_pnl.is_finite()
                or not position.unrealized_pnl.is_finite()
            ):
                violations.append(_violation(
                    RiskViolationCode.POSITION_INVARIANT,
                    "position accounting values must be finite",
                ))
            if position.quantity == ZERO and position.average_price != ZERO:
                violations.append(_violation(
                    RiskViolationCode.POSITION_INVARIANT,
                    "flat positions must have zero average price",
                    str(position.average_price),
                    "0",
                ))
            if position.quantity != ZERO and position.average_price <= ZERO:
                violations.append(_violation(
                    RiskViolationCode.POSITION_INVARIANT,
                    "open positions require a positive average price",
                    str(position.average_price),
                    "> 0",
                ))
        return violations

    def _margin_violations(self, snapshot: MarginAccountSnapshot, account_id: str) -> list[RiskViolation]:
        violations: list[RiskViolation] = []
        if snapshot.account_id != account_id:
            violations.append(_violation(
                RiskViolationCode.SNAPSHOT_ACCOUNT_MISMATCH,
                "margin snapshot account does not match the assessed account",
                snapshot.account_id,
                account_id,
            ))
        if snapshot.symbol != self.policy.symbol:
            violations.append(_violation(
                RiskViolationCode.SNAPSHOT_SYMBOL_MISMATCH,
                "margin snapshot symbol does not match the risk policy",
                snapshot.symbol,
                self.policy.symbol,
            ))
        if snapshot.currency != self.policy.settlement_currency:
            violations.append(_violation(
                RiskViolationCode.SNAPSHOT_CURRENCY_MISMATCH,
                "margin snapshot currency does not match the risk policy",
                snapshot.currency,
                self.policy.settlement_currency,
            ))
        if snapshot.liquidatable or snapshot.margin_excess <= ZERO:
            violations.append(_violation(
                RiskViolationCode.MARGIN_INVARIANT,
                "post-trade account is at or below the liquidation threshold",
                str(snapshot.margin_excess),
                "> 0",
            ))
        return violations

    def _engage_automatic(self, *, event_id: str, occurred_at: datetime, reason: str) -> None:
        if not self.kill_switch_engaged:
            self.engage_kill_switch(
                event_id=event_id,
                occurred_at=occurred_at,
                reason=reason,
                source=KillSwitchSource.AUTOMATIC,
            )

    def _append_decision(
        self,
        *,
        decision_id: str,
        kind: RiskDecisionKind,
        occurred_at: datetime,
        account_id: str,
        symbol: str,
        decision: RiskDecision,
        violations: tuple[RiskViolation, ...],
        metrics: tuple[RiskMetric, ...],
    ) -> RiskDecisionRecord:
        record = RiskDecisionRecord(
            sequence=self._reserve_audit(decision_id, occurred_at),
            decision_id=decision_id,
            kind=kind,
            occurred_at=occurred_at,
            account_id=account_id,
            symbol=symbol,
            policy_id=self.policy.policy_id,
            decision=decision,
            violations=violations,
            metrics=metrics,
        )
        self._decisions.append(record)
        return record

    def _new_kill_event(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        state: KillSwitchState,
        source: KillSwitchSource,
        reason: str,
    ) -> KillSwitchEvent:
        return KillSwitchEvent(
            sequence=self._reserve_audit(event_id, occurred_at),
            event_id=event_id,
            occurred_at=occurred_at,
            state=state,
            source=source,
            reason=reason,
        )

    def _reserve_audit(self, audit_id: str, occurred_at: datetime) -> int:
        _text(audit_id, "audit_id")
        _aware(occurred_at, "occurred_at")
        if audit_id in self._audit_ids:
            raise ValueError(f"audit ID already exists: {audit_id}")
        if self._last_audit_time is not None and occurred_at < self._last_audit_time:
            raise ValueError("risk audit timestamps must be non-decreasing")
        self._audit_sequence += 1
        self._audit_ids.add(audit_id)
        self._last_audit_time = occurred_at
        return self._audit_sequence


def _max_order_for_position(
    current_quantity: Decimal,
    direction: Decimal,
    maximum_absolute_quantity: Decimal,
) -> Decimal:
    if direction not in {ONE, -ONE}:
        raise ValueError("direction must be +1 or -1")
    signed_current = current_quantity * direction
    if signed_current >= ZERO:
        return max(ZERO, maximum_absolute_quantity - abs(current_quantity))
    return abs(current_quantity) + maximum_absolute_quantity


def _position_quantity(state: ExecutionState, account_id: str, symbol: str) -> Decimal:
    values = [
        position.quantity
        for position in state.positions
        if position.account_id == account_id and position.symbol == symbol
    ]
    if len(values) > 1:
        raise ValueError("execution state contains duplicate account-symbol positions")
    return values[0] if values else ZERO


def _cash_balance(state: ExecutionState, account_id: str, currency: str) -> Decimal:
    values = [
        balance.balance
        for balance in state.cash
        if balance.account_id == account_id and balance.currency == currency
    ]
    if len(values) > 1:
        raise ValueError("execution state contains duplicate account-currency balances")
    return values[0] if values else ZERO


def _decision_dict(record: RiskDecisionRecord) -> dict[str, object]:
    return {
        "account_id": record.account_id,
        "decision": {
            "allowed": record.decision.allowed,
            "reason": record.decision.reason,
            "size_multiplier": record.decision.size_multiplier,
        },
        "decision_id": record.decision_id,
        "kind": record.kind.value,
        "metrics": [{"name": item.name, "value": item.value} for item in record.metrics],
        "occurred_at": record.occurred_at.isoformat(),
        "policy_id": record.policy_id,
        "sequence": record.sequence,
        "symbol": record.symbol,
        "violations": [
            {
                "actual": item.actual,
                "code": item.code.value,
                "limit": item.limit,
                "message": item.message,
            }
            for item in record.violations
        ],
    }


def _reason(violations: list[RiskViolation]) -> str:
    return "; ".join(item.code.value for item in violations)


def _violation(
    code: RiskViolationCode,
    message: str,
    actual: str = "",
    limit: str = "",
) -> RiskViolation:
    return RiskViolation(code=code, message=message, actual=actual, limit=limit)


def _seconds(value: timedelta) -> str:
    return str(Decimal(str(value.total_seconds())))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in hexdigits for character in normalized):
        raise ValueError(f"{name} must be a 64-character hexadecimal digest")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _nonnegative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be non-negative")
