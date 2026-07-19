"""Deterministic single-strategy paper-trading session orchestration."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from .contracts import AlphaPlugin, Signal
from .execution_engine import CashFlowKind, CashSettlementMode, EventSourcedExecutionEngine
from .execution_profiles import ExecutionProfileSnapshot, floor_to_increment
from .finance import ExecutionOrderType, OrderSide
from .paper_contracts import (
    PaperCycleRequest,
    PaperCycleResult,
    PaperCycleStatus,
    PaperLaunchAuthorization,
)
from .risk_engine import (
    BrokerSnapshot,
    KillSwitchEvent,
    KillSwitchSource,
    PreTradeRiskRequest,
    ReconciliationResult,
    RiskCheckpoint,
    RiskDecisionRecord,
    SingleStrategyRiskEngine,
    SingleStrategyRiskPolicy,
)
from .strategy_decision import StrategyDecisionPackage, StrategyLifecycleState
from .venue_simulator import (
    DeterministicVenueSimulator,
    VenueFillEvidence,
    VenueOrderRecord,
    VenueOrderRequest,
    VenueOrderStatus,
    VenueSimulationConfig,
)

ZERO = Decimal("0")


class PaperTradingOrchestrator:
    """Run one approved point-in-time strategy through deterministic paper cycles."""

    def __init__(
        self,
        *,
        plugin: AlphaPlugin,
        decision_package: StrategyDecisionPackage,
        authorization: PaperLaunchAuthorization,
        venue_profile: ExecutionProfileSnapshot,
        risk_policy: SingleStrategyRiskPolicy,
        venue_config: VenueSimulationConfig | None = None,
    ) -> None:
        config = venue_config or VenueSimulationConfig()
        self._validate_launch(
            plugin=plugin,
            decision_package=decision_package,
            authorization=authorization,
            venue_profile=venue_profile,
            risk_policy=risk_policy,
            venue_config=config,
        )
        self.plugin = plugin
        self.decision_package = decision_package
        self.authorization = authorization
        self.engine = EventSourcedExecutionEngine()
        self.venue = DeterministicVenueSimulator(
            venue_profile,
            config=config,
            engine=self.engine,
        )
        self.risk = SingleStrategyRiskEngine(risk_policy)
        self._cycles: list[PaperCycleResult] = []
        self._cycle_ids: set[str] = set()
        self.engine.adjust_cash(
            event_id=f"paper-{authorization.session_id}-initial-cash",
            occurred_at=authorization.authorized_at,
            account_id=authorization.account_id,
            currency=authorization.settlement_currency,
            amount=authorization.initial_cash,
            kind=CashFlowKind.DEPOSIT,
            reason="paper session initial collateral",
        )
        self._latest_checkpoint = RiskCheckpoint.from_engine(
            checkpoint_id=f"paper-{authorization.session_id}-launch",
            created_at=authorization.authorized_at,
            engine=self.engine,
        )

    @property
    def cycles(self) -> tuple[PaperCycleResult, ...]:
        return tuple(self._cycles)

    @property
    def latest_checkpoint(self) -> RiskCheckpoint:
        return self._latest_checkpoint

    def run_cycle(self, request: PaperCycleRequest) -> PaperCycleResult:
        if request.cycle_id in self._cycle_ids:
            raise ValueError(f"paper cycle already exists: {request.cycle_id}")
        self._validate_cycle_request(request)
        event_start = len(self.engine.events)
        signal = self.plugin.generate(request.completed_bars)
        self._validate_signal(signal, request)

        current_position = self._position_quantity()
        if current_position != ZERO:
            self.engine.mark_price(
                event_id=f"paper-{request.cycle_id}-decision-mark",
                occurred_at=request.decision_quote.observed_at,
                account_id=self.authorization.account_id,
                symbol=self.authorization.symbol,
                price=request.decision_quote.trade_price,
            )
        equity = self._equity()
        desired_position = (
            Decimal(str(signal.score)) * equity / request.decision_quote.trade_price
        )
        requested_quantity = abs(desired_position - current_position)
        if requested_quantity == ZERO:
            return self._finish(
                request=request,
                signal=signal,
                status=PaperCycleStatus.NO_ACTION,
                equity=equity,
                current_position=current_position,
                desired_position=desired_position,
                requested_quantity=ZERO,
                reason="strategy target already matches the paper position",
                event_start=event_start,
            )

        side = OrderSide.BUY if desired_position > current_position else OrderSide.SELL
        reference_price = (
            request.decision_quote.ask_price
            if side is OrderSide.BUY
            else request.decision_quote.bid_price
        )
        pre_trade = self.risk.pre_trade(
            PreTradeRiskRequest(
                decision_id=f"paper-{request.cycle_id}-pre-trade",
                occurred_at=request.occurred_at,
                account_id=self.authorization.account_id,
                symbol=self.authorization.symbol,
                side=side,
                quantity=requested_quantity,
                reference_price=reference_price,
                data_observed_at=request.decision_quote.observed_at,
                current_position_quantity=current_position,
                equity=equity,
                daily_pnl=request.daily_pnl,
                reduce_only=request.reduce_only,
            )
        )
        if not pre_trade.decision.allowed:
            return self._finish(
                request=request,
                signal=signal,
                status=PaperCycleStatus.RISK_REJECTED,
                equity=equity,
                current_position=current_position,
                desired_position=desired_position,
                requested_quantity=requested_quantity,
                pre_trade=pre_trade,
                reason=pre_trade.decision.reason,
                event_start=event_start,
            )

        submitted_quantity = requested_quantity * Decimal(
            str(pre_trade.decision.size_multiplier)
        )
        increment = _market_quantity_increment(self.venue.snapshot)
        if increment is not None:
            submitted_quantity = floor_to_increment(submitted_quantity, increment)
        if submitted_quantity <= ZERO:
            return self._finish(
                request=request,
                signal=signal,
                status=PaperCycleStatus.BELOW_VENUE_INCREMENT,
                equity=equity,
                current_position=current_position,
                desired_position=desired_position,
                requested_quantity=requested_quantity,
                pre_trade=pre_trade,
                reason="risk-approved quantity is below the venue increment",
                event_start=event_start,
            )

        order_id = f"paper-{self.authorization.session_id}-{request.cycle_id}-order"
        order = self.venue.submit(
            VenueOrderRequest(
                order_id=order_id,
                intent_id=f"paper-{request.cycle_id}-intent",
                account_id=self.authorization.account_id,
                symbol=self.authorization.symbol,
                side=side,
                quantity=submitted_quantity,
                order_type=ExecutionOrderType.MARKET,
                submitted_at=request.occurred_at,
            ),
            reference_quote=request.decision_quote,
        )
        if order.status is VenueOrderStatus.REJECTED:
            post = self.risk.assess_post_trade(
                decision_id=f"paper-{request.cycle_id}-post-trade",
                occurred_at=request.occurred_at,
                account_id=self.authorization.account_id,
                engine=self.engine,
            )
            return self._finish(
                request=request,
                signal=signal,
                status=PaperCycleStatus.VENUE_REJECTED,
                equity=equity,
                current_position=current_position,
                desired_position=desired_position,
                requested_quantity=requested_quantity,
                submitted_quantity=submitted_quantity,
                pre_trade=pre_trade,
                venue_order=order,
                post_trade=post,
                reason=order.reason,
                event_start=event_start,
            )

        matched = self.venue.match(order_id, request.match_quote)
        final_order = matched.order
        if matched.remaining_quantity > ZERO:
            final_order = self.venue.cancel(
                order_id,
                occurred_at=request.match_quote.observed_at,
                reason="single-quote paper cycle cancels unfilled remainder",
            )
        if matched.fill is not None and self._position_quantity() != ZERO:
            self.engine.mark_price(
                event_id=f"paper-{request.cycle_id}-post-fill-mark",
                occurred_at=request.match_quote.observed_at,
                account_id=self.authorization.account_id,
                symbol=self.authorization.symbol,
                price=request.match_quote.trade_price,
            )
        post = self.risk.assess_post_trade(
            decision_id=f"paper-{request.cycle_id}-post-trade",
            occurred_at=request.match_quote.observed_at,
            account_id=self.authorization.account_id,
            engine=self.engine,
        )
        status, reason, kill_event = self._completion_status(
            cycle_id=request.cycle_id,
            occurred_at=request.match_quote.observed_at,
            post_trade=post,
            fill=matched.fill,
            remaining_quantity=matched.remaining_quantity,
        )
        return self._finish(
            request=request,
            signal=signal,
            status=status,
            equity=equity,
            current_position=current_position,
            desired_position=desired_position,
            requested_quantity=requested_quantity,
            submitted_quantity=submitted_quantity,
            executed_quantity=ZERO if matched.fill is None else matched.fill.quantity,
            pre_trade=pre_trade,
            venue_order=final_order,
            venue_fill=matched.fill,
            post_trade=post,
            kill_event=kill_event,
            reason=reason,
            event_start=event_start,
            checkpoint_at=request.match_quote.observed_at,
        )

    def reconcile(
        self,
        *,
        decision_id: str,
        occurred_at: datetime,
        broker_snapshot: BrokerSnapshot,
    ) -> ReconciliationResult:
        _aware(occurred_at, "occurred_at")
        if broker_snapshot.observed_at > occurred_at:
            raise ValueError("broker snapshot must not be observed after reconciliation")
        return self.risk.reconcile(
            decision_id=decision_id,
            occurred_at=occurred_at,
            engine=self.engine,
            broker=broker_snapshot,
        )

    def session_json(self) -> str:
        payload = {
            "authorization": self.authorization.to_dict(),
            "cycles": [cycle.to_dict() for cycle in self._cycles],
            "execution_events": json.loads(self.engine.events_json()),
            "execution_state": json.loads(self.engine.state.to_json()),
            "latest_checkpoint": {
                "checkpoint_id": self.latest_checkpoint.checkpoint_id,
                "created_at": self.latest_checkpoint.created_at.isoformat(),
                "event_log_sha256": self.latest_checkpoint.event_log_sha256,
                "event_sequence": self.latest_checkpoint.event_sequence,
                "state_sha256": self.latest_checkpoint.state_sha256,
            },
            "risk_audit": json.loads(self.risk.audit_json()),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _completion_status(
        self,
        *,
        cycle_id: str,
        occurred_at: datetime,
        post_trade: RiskDecisionRecord,
        fill: VenueFillEvidence | None,
        remaining_quantity: Decimal,
    ) -> tuple[PaperCycleStatus, str, KillSwitchEvent | None]:
        if not post_trade.decision.allowed:
            event = None
            if not self.risk.kill_switch_engaged:
                event = self.risk.engage_kill_switch(
                    event_id=f"paper-{cycle_id}-automatic-kill",
                    occurred_at=occurred_at,
                    reason="paper post-trade risk gate failed",
                    source=KillSwitchSource.AUTOMATIC,
                )
            return PaperCycleStatus.POST_TRADE_BLOCKED, post_trade.decision.reason, event
        if fill is None:
            return (
                PaperCycleStatus.NO_FILL,
                "venue produced no fill for the single match quote",
                None,
            )
        if remaining_quantity > ZERO:
            return (
                PaperCycleStatus.PARTIALLY_FILLED,
                "venue partially filled the order and the remainder was cancelled",
                None,
            )
        return (
            PaperCycleStatus.FILLED,
            "paper cycle filled and passed post-trade risk",
            None,
        )

    def _finish(
        self,
        *,
        request: PaperCycleRequest,
        signal: Signal,
        status: PaperCycleStatus,
        equity: Decimal,
        current_position: Decimal,
        desired_position: Decimal,
        requested_quantity: Decimal,
        reason: str,
        event_start: int,
        submitted_quantity: Decimal = ZERO,
        executed_quantity: Decimal = ZERO,
        pre_trade: RiskDecisionRecord | None = None,
        venue_order: VenueOrderRecord | None = None,
        venue_fill: VenueFillEvidence | None = None,
        post_trade: RiskDecisionRecord | None = None,
        kill_event: KillSwitchEvent | None = None,
        checkpoint_at: datetime | None = None,
    ) -> PaperCycleResult:
        checkpoint = RiskCheckpoint.from_engine(
            checkpoint_id=f"paper-{request.cycle_id}-checkpoint",
            created_at=checkpoint_at or request.occurred_at,
            engine=self.engine,
        )
        result = PaperCycleResult(
            cycle_id=request.cycle_id,
            session_id=self.authorization.session_id,
            status=status,
            occurred_at=request.occurred_at,
            signal=signal,
            equity_before=equity,
            current_position_quantity=current_position,
            desired_position_quantity=desired_position,
            requested_order_quantity=requested_quantity,
            submitted_order_quantity=submitted_quantity,
            executed_quantity=executed_quantity,
            pre_trade_decision=pre_trade,
            venue_order=venue_order,
            venue_fill=venue_fill,
            post_trade_decision=post_trade,
            kill_switch_event=kill_event,
            checkpoint=checkpoint,
            event_sequence_start=event_start,
            event_sequence_end=len(self.engine.events),
            reason=reason,
        )
        self._cycles.append(result)
        self._cycle_ids.add(request.cycle_id)
        self._latest_checkpoint = checkpoint
        return result

    def _validate_launch(
        self,
        *,
        plugin: AlphaPlugin,
        decision_package: StrategyDecisionPackage,
        authorization: PaperLaunchAuthorization,
        venue_profile: ExecutionProfileSnapshot,
        risk_policy: SingleStrategyRiskPolicy,
        venue_config: VenueSimulationConfig,
    ) -> None:
        if decision_package.state is not StrategyLifecycleState.PAPER:
            raise ValueError("paper session requires a decision package in PAPER state")
        if decision_package.content_sha256() != authorization.decision_package_sha256:
            raise ValueError("paper authorization does not match the decision package")
        if decision_package.package_id != authorization.decision_package_id:
            raise ValueError("paper authorization package ID mismatch")
        if decision_package.strategy_id != authorization.strategy_id:
            raise ValueError("paper authorization strategy ID mismatch")
        if plugin.name != authorization.strategy_id:
            raise ValueError("plugin name must match the authorized strategy ID")
        if venue_profile.snapshot_id != authorization.venue_profile_snapshot_id:
            raise ValueError("paper authorization venue profile mismatch")
        if risk_policy.policy_id != authorization.risk_policy_id:
            raise ValueError("paper authorization risk policy mismatch")
        if venue_profile.rules.symbol != authorization.symbol:
            raise ValueError("paper authorization symbol mismatch")
        if risk_policy.symbol != authorization.symbol:
            raise ValueError("risk policy symbol mismatch")
        if venue_profile.profile.settlement_currency != authorization.settlement_currency:
            raise ValueError("paper authorization settlement currency mismatch")
        if risk_policy.settlement_currency != authorization.settlement_currency:
            raise ValueError("risk policy settlement currency mismatch")
        if venue_config.settlement_mode is not CashSettlementMode.DERIVATIVE_PNL_ONLY:
            raise ValueError(
                "paper reference orchestrator requires derivative PnL-only settlement"
            )
        if authorization.authorized_at < venue_profile.effective_from or (
            venue_profile.effective_to is not None
            and authorization.authorized_at >= venue_profile.effective_to
        ):
            raise ValueError("venue profile is not effective at paper authorization")

    def _validate_cycle_request(self, request: PaperCycleRequest) -> None:
        bars = request.completed_bars
        timestamps = tuple(bar.timestamp for bar in bars)
        if timestamps != tuple(sorted(timestamps)) or len(timestamps) != len(set(timestamps)):
            raise ValueError("completed bars must have unique increasing timestamps")
        if any(bar.symbol != self.authorization.symbol for bar in bars):
            raise ValueError("completed bar symbol does not match paper authorization")
        if any(
            bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None
            for bar in bars
        ):
            raise ValueError("completed bar timestamps must be timezone-aware")
        if request.decision_quote.symbol != self.authorization.symbol:
            raise ValueError("decision quote symbol does not match paper authorization")
        if request.match_quote.symbol != self.authorization.symbol:
            raise ValueError("match quote symbol does not match paper authorization")
        if bars[-1].timestamp > request.decision_quote.observed_at:
            raise ValueError("decision quote must not precede the newest completed bar")
        if request.decision_quote.observed_at > request.occurred_at:
            raise ValueError("decision quote must not be observed after cycle time")
        accepted_at = request.occurred_at + self.venue.config.order_latency
        if request.match_quote.observed_at < accepted_at:
            raise ValueError("match quote must not precede deterministic order arrival")
        if request.occurred_at < self.authorization.authorized_at:
            raise ValueError("paper cycle must not precede session authorization")
        if (
            self.engine.events
            and request.decision_quote.observed_at < self.engine.events[-1].occurred_at
        ):
            raise ValueError("paper market data must not regress behind execution events")

    def _validate_signal(self, signal: Signal, request: PaperCycleRequest) -> None:
        if signal.symbol != self.authorization.symbol:
            raise ValueError("strategy signal symbol does not match paper authorization")
        if signal.generated_at != request.completed_bars[-1].timestamp:
            raise ValueError("strategy signal must reference the newest completed bar")
        if not signal.source.strip():
            raise ValueError("strategy signal source must not be empty")

    def _position_quantity(self) -> Decimal:
        for position in self.engine.state.positions:
            if (
                position.account_id == self.authorization.account_id
                and position.symbol == self.authorization.symbol
            ):
                return position.quantity
        return ZERO

    def _equity(self) -> Decimal:
        cash = ZERO
        for balance in self.engine.state.cash:
            if (
                balance.account_id == self.authorization.account_id
                and balance.currency == self.authorization.settlement_currency
            ):
                cash = balance.balance
                break
        unrealized = ZERO
        for position in self.engine.state.positions:
            if (
                position.account_id == self.authorization.account_id
                and position.symbol == self.authorization.symbol
            ):
                unrealized = position.unrealized_pnl
                break
        return cash + unrealized


def _market_quantity_increment(snapshot: ExecutionProfileSnapshot) -> Decimal | None:
    if snapshot.rules.market_lot_size_overrides:
        return snapshot.rules.market_quantity_step
    return snapshot.rules.quantity_step


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = ["PaperTradingOrchestrator"]
