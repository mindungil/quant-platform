"""
LangGraph-style StateGraph -- pure Python implementation.

The pipeline is a directed graph of async node functions.  Each node receives
the current ``AgentState``, mutates it, and returns it.  Edge routing is
determined by simple conditionals after key nodes.

Node order:
  gather_context -> retrieve_memory -> select_strategy -> apply_strategy
      -> check_risk -> execute_order -> record_decision

Short-circuit edges:
  - Signal below threshold  ->  action=HOLD, jump to record_decision
  - Risk rejected           ->  action=SKIP, jump to record_decision
  - Any node failure        ->  action=ERROR, jump to record_decision
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from app.core.config import settings
from app.core import metrics as m
from app.models.decision import (
    Action,
    AgentState,
    DecisionRecord,
    MemoryRecord,
    MemorySearchRequest,
    MemorySearchResult,
)
from app.services import (
    feature_client,
    memory_client,
    order_client,
    risk_client,
    signal_client,
    strategy_client,
)
from app.services.reasoning import generate_reasoning

logger = logging.getLogger(__name__)

# Type alias
NodeFn = Callable[[AgentState], Awaitable[AgentState]]


# ---------------------------------------------------------------------------
# StateGraph executor
# ---------------------------------------------------------------------------

class StateGraph:
    """
    Minimal directed-graph executor.  Nodes are async callables that receive
    and return an ``AgentState``.  Edges are evaluated via a routing function
    after each node, which returns the name of the next node (or ``None`` to
    stop).
    """

    def __init__(self) -> None:
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str | Callable[[AgentState], str | None]] = {}
        self._entry: str | None = None

    def add_node(self, name: str, fn: NodeFn) -> None:
        self._nodes[name] = fn

    def add_edge(self, from_node: str, to: str | Callable[[AgentState], str | None]) -> None:
        """
        ``to`` may be a fixed node name *or* a callable that inspects state
        and returns the next node name (or ``None`` to halt).
        """
        self._edges[from_node] = to

    def set_entry(self, name: str) -> None:
        self._entry = name

    async def run(self, state: AgentState) -> AgentState:
        if self._entry is None:
            raise RuntimeError("No entry node set on StateGraph")

        current = self._entry
        visited: list[str] = []

        while current is not None:
            if current not in self._nodes:
                raise RuntimeError(f"Unknown node: {current}")

            node_fn = self._nodes[current]
            visited.append(current)

            try:
                state = await node_fn(state)
            except Exception:
                logger.exception("Node '%s' failed for %s", current, state.asset)
                state.error = str(current)
                state.step = current
                state.action = Action.ERROR.value
                m.PIPELINE_ERRORS.labels(step=current).inc()
                current = "record_decision"
                continue

            # Resolve next node
            edge = self._edges.get(current)
            if edge is None:
                break
            if callable(edge):
                current = edge(state)
            else:
                current = edge

        logger.debug("Pipeline path for %s: %s", state.asset, " -> ".join(visited))
        return state


# ---------------------------------------------------------------------------
# Singleton client instances (lazy)
# ---------------------------------------------------------------------------

_signal_client = None
_memory_client = None
_strategy_client = None


def _get_signal_client():
    global _signal_client
    if _signal_client is None:
        from app.services.signal_client import SignalClient
        _signal_client = SignalClient(settings.signal_service_base_url)
    return _signal_client


def _get_memory_client():
    global _memory_client
    if _memory_client is None:
        from app.services.memory_client import MemoryClient
        _memory_client = MemoryClient(settings.memory_service_base_url)
    return _memory_client


def _get_strategy_client():
    global _strategy_client
    if _strategy_client is None:
        from app.services.strategy_client import StrategyClient
        _strategy_client = StrategyClient(settings.strategy_registry_base_url)
    return _strategy_client


# ---------------------------------------------------------------------------
# Pipeline nodes
# ---------------------------------------------------------------------------

async def gather_context(state: AgentState) -> AgentState:
    """Fetch latest signal and features in parallel."""
    signal_task = asyncio.create_task(
        asyncio.to_thread(_get_signal_client().get_latest_signal, state.asset, user_id=state.user_id)
    )
    feature_task = asyncio.create_task(_safe_fetch_features(state.asset))

    signal = await signal_task
    features = await feature_task

    state.signal = signal
    state.features = features

    if signal and signal.components:
        state.signal = signal
        m.LAST_SIGNAL_SCORE.labels(asset=state.asset).set(signal.signal_score)

    return state


async def _safe_fetch_features(asset: str) -> dict[str, Any] | None:
    """Fetch features, returning None on failure (non-critical)."""
    try:
        return await feature_client.get_latest_features(asset)
    except Exception:
        logger.warning("Feature fetch failed for %s - continuing without features", asset)
        return None


async def retrieve_memory(state: AgentState) -> AgentState:
    """Search memory-service for the top-5 similar past episodes."""
    try:
        request = MemorySearchRequest(
            asset=state.asset,
            asset_type="crypto",
            signal_score=state.signal.signal_score if state.signal else 0.0,
            action="BUY",
            strategy_id=state.strategy.name if state.strategy else None,
            top_k=5,
        )
        response = _get_memory_client().search(request)
        state.memories = list(response.items)
        state.memory_refs = [item.record.asset for item in response.items]
    except Exception:
        logger.warning("Memory search failed for %s - continuing without memory", state.asset)
        state.memories = []
        state.memory_refs = []

    return state


async def select_strategy(state: AgentState) -> AgentState:
    """Fetch strategy for the current lane.

    - agent_core lane: existing behavior — fetch user's ACTIVE strategy from
      registry (falls back to bootstrap).
    - user_template lane: build a synthetic strategy from the subscription's
      template definition. state.template_id must be set by the caller.
    """
    client = _get_strategy_client()
    if state.lane == "user_template":
        template = None
        if state.template_id:
            template = client.get_template(state.template_id)
        if template is None:
            logger.warning("Template %s not found for subscription %s — holding",
                           state.template_id, state.subscription_id)
            state.strategy = None
            state.action = Action.HOLD.value
            return state
        # Build synthetic StrategyData for the template lane
        from app.models.decision import StrategyData
        state.strategy = StrategyData(
            name=f"template:{template.get('name', state.template_id)}",
            asset_type=template.get("asset_type", "crypto"),
            indicators=list(template.get("factors", [])),
            weights=dict(template.get("weights", {})),
            thresholds={"entry": 0.6, "exit": -0.35},
            version="template-v1",
            status="ACTIVE",
            lane="user_template",
            subscription_id=state.subscription_id,
            template_id=state.template_id,
        )
        return state
    # agent_core
    strategy = client.get_active_strategy("crypto", user_id=state.user_id)
    state.strategy = strategy
    return state


async def apply_strategy(state: AgentState) -> AgentState:
    """
    Apply strategy weights to signal components and adjust score based on
    memory insights.

    Weighting:
      - For each indicator the strategy specifies a weight, the rest of
        the signal score is the weighted combination.
      - Memory adjustment: if a majority of similar past episodes were
        profitable, nudge the score up by a small factor; if mostly
        unprofitable, nudge down.
    """
    if state.signal is None:
        logger.warning("No signal data available to apply strategy")
        state.action = Action.HOLD.value
        return state

    raw_score = state.signal.signal_score

    # Weighted combination from strategy weights
    if state.strategy and state.strategy.weights and state.signal.components:
        weighted_sum = 0.0
        total_weight = 0.0
        for indicator, weight in state.strategy.weights.items():
            if indicator in state.signal.components:
                weighted_sum += state.signal.components[indicator] * weight
                total_weight += abs(weight)
        strategy_score = weighted_sum / total_weight if total_weight > 0 else raw_score
        state.strategy_weighted_score = strategy_score
    else:
        strategy_score = raw_score
        state.strategy_weighted_score = raw_score

    # Memory adjustment
    memory_adjustment = 0.0
    if state.memories and len(state.memories) > 0:
        total = len(state.memories)
        profitable_count = 0
        for mem in state.memories:
            meta = mem.record.metadata or {}
            outcome = meta.get("outcome") or meta.get("profit")
            if outcome and (
                (isinstance(outcome, str) and outcome == "profitable")
                or (isinstance(outcome, (int, float)) and outcome > 0)
            ):
                profitable_count += 1

        profit_ratio = profitable_count / total
        if profit_ratio >= 0.6:
            memory_adjustment = 0.05
            state.memory_insight = (
                f"Memory boost: {profitable_count}/{total} similar episodes "
                f"were profitable (+{memory_adjustment:+.4f} adjustment)."
            )
        elif profit_ratio < 0.4:
            memory_adjustment = -0.03
            state.memory_insight = (
                f"Memory caution: only {profitable_count}/{total} similar episodes "
                f"were profitable ({memory_adjustment:+.4f} adjustment)."
            )

    adjusted = strategy_score + memory_adjustment
    state.adjusted_score = adjusted

    # Threshold check
    threshold = 0.6
    if state.strategy and state.strategy.thresholds:
        strategy_threshold = state.strategy.thresholds.get("entry", 0.6)
        effective_threshold = strategy_threshold
    else:
        effective_threshold = threshold

    direction = state.signal.direction if state.signal else "HOLD"
    if abs(adjusted) >= effective_threshold:
        state.action = "SELL" if direction in ("SELL",) else "BUY"
    else:
        state.action = Action.HOLD.value

    return state


async def check_risk(state: AgentState) -> AgentState:
    """Call risk-service for pre-trade approval."""
    result = await risk_client.check_risk(state.asset)
    state.risk_approval = result
    if not result.approved:
        state.action = Action.SKIP.value
        m.RISK_REJECTIONS.inc()

    return state


async def execute_order(state: AgentState) -> AgentState:
    """Submit order via order-service (or simulate in shadow mode).

    Order notional is scaled by state.lane_budget_pct so each lane trades
    only its allocated slice of capital.
    """
    result = await order_client.submit_order(
        asset=state.asset,
        side=state.action,
        shadow_mode=settings.shadow_mode,
        user_id=state.user_id,
        lane=state.lane,
        lane_budget_pct=state.lane_budget_pct,
        subscription_id=state.subscription_id,
        template_id=state.template_id,
        strategy_id=(state.signal.strategy_id if state.signal else None),
        agent_name="crypto-agent",
    )
    state.order_result = result

    mode_label = "shadow" if result.shadow_mode else "live"
    m.ORDERS_SUBMITTED.labels(mode=mode_label).inc()

    return state


async def record_decision(state: AgentState) -> AgentState:
    """
    Persist the full DecisionRecord to PostgreSQL and record as an episode
    in memory-service.
    """
    strategy_dict = state.strategy
    strategy_name = strategy_dict.name if strategy_dict else None

    decision = DecisionRecord(
        id=str(uuid.uuid4()),
        asset=state.asset,
        agent_type="crypto",
        user_id=state.user_id or settings.default_user_id,
        signal_score=state.signal.signal_score if state.signal else None,
        direction=state.signal.direction if state.signal else None,
        action=state.action,
        strategy=strategy_name,
        memory_refs=state.memory_refs,
        reasoning=generate_reasoning(state),
        risk_approved=state.risk_approval.approved if state.risk_approval else None,
        order_id=state.order_result.order_id if state.order_result else None,
        decided_at=datetime.now(timezone.utc),
        shadow_mode=state.order_result.shadow_mode if state.order_result else None,
    )
    state.decision = decision

    m.DECISIONS_TOTAL.labels(action=state.action or "UNKNOWN").inc()

    # Persist to DB
    try:
        from app.db import repository as repo
        repo.decision_repository.save(state.asset, decision)
    except Exception:
        logger.exception("Failed to persist decision %s", decision.id)

    # Record episode in memory-service
    try:
        episode = MemoryRecord(
            asset=state.asset,
            asset_type="crypto",
            signal_score=state.signal.signal_score if state.signal else 0.0,
            action=state.action,
            strategy_id=strategy_name,
            reasoning=decision.reasoning,
            user_id=decision.user_id,
            metadata={
                "decision_id": decision.id,
                "risk_approved": decision.risk_approved,
                "risk_reason": state.risk_approval.reason if state.risk_approval else None,
                "order_id": decision.order_id,
                "shadow_mode": decision.shadow_mode,
                "decided_at": decision.decided_at.isoformat(),
            },
        )
        _get_memory_client().record(episode)
    except Exception:
        logger.exception("Failed to record episode in memory-service for %s", state.asset)

    return state


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def _route_after_apply_strategy(state: AgentState) -> str | None:
    """After apply_strategy, decide whether to check risk or skip to record."""
    if state.action == Action.HOLD.value:
        return "record_decision"
    return "check_risk"


def _route_after_check_risk(state: AgentState) -> str | None:
    """After check_risk, decide whether to execute or skip to record."""
    if state.action == Action.SKIP.value:
        return "record_decision"
    return "execute_order"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_pipeline() -> StateGraph:
    """Construct and return the decision pipeline StateGraph."""
    graph = StateGraph()

    graph.add_node("gather_context", gather_context)
    graph.add_node("retrieve_memory", retrieve_memory)
    graph.add_node("select_strategy", select_strategy)
    graph.add_node("apply_strategy", apply_strategy)
    graph.add_node("check_risk", check_risk)
    graph.add_node("execute_order", execute_order)
    graph.add_node("record_decision", record_decision)

    graph.set_entry("gather_context")
    graph.add_edge("gather_context", "retrieve_memory")
    graph.add_edge("retrieve_memory", "select_strategy")
    graph.add_edge("select_strategy", "apply_strategy")
    graph.add_edge("apply_strategy", _route_after_apply_strategy)
    graph.add_edge("check_risk", _route_after_check_risk)
    graph.add_edge("execute_order", "record_decision")
    # record_decision has no outgoing edge (terminal)

    return graph


_pipeline = build_pipeline()


async def run_pipeline(asset: str, *, lane: str = "agent_core",
                       lane_budget_pct: float = 1.0,
                       subscription_id: str | None = None,
                       template_id: str | None = None,
                       user_id: str | None = None) -> AgentState:
    """
    Execute the full decision pipeline for a given asset on a given lane.
    Returns the final AgentState with all fields populated.
    """
    state = AgentState(
        asset=asset,
        lane=lane,
        lane_budget_pct=lane_budget_pct,
        subscription_id=subscription_id,
        template_id=template_id,
        user_id=user_id,
    )
    start = time.monotonic()
    state = await _pipeline.run(state)
    elapsed = time.monotonic() - start
    m.PIPELINE_DURATION.observe(elapsed)
    logger.info(
        "Pipeline[%s] completed for %s in %.3fs -> action=%s",
        lane, state.asset, elapsed, state.action,
    )
    return state


async def run_dual_lane_pipeline(asset: str) -> list[AgentState]:
    """Run both lanes for a single asset, fanning out across users.

    Flow:
      1. Agent core lane runs ONCE for the platform default user (system
         account that executes the validated engine).
      2. Template lane fans out: fetches every enabled subscription across
         all users, groups by user, and per-user runs one pipeline per
         subscribed template using that user's lane_allocation and weight.

    Returns a flat list of AgentState (one per lane run). Collision
    detection is done per-user across agent vs template lanes and publishes
    alerts but does NOT net orders.
    """
    client = _get_strategy_client()
    results: list[AgentState] = []

    # ----- Agent core lane (platform-level, single run) -----
    try:
        default_alloc = client.get_allocation("crypto")
        default_agent_pct = float(default_alloc.get("agent_pct", 0.70))
    except Exception:
        default_agent_pct = 0.70

    if default_agent_pct > 0:
        try:
            results.append(await run_pipeline(
                asset, lane="agent_core", lane_budget_pct=default_agent_pct, user_id=settings.default_user_id,
            ))
        except Exception:
            logger.exception("agent_core lane failed for %s", asset)

    # ----- Template lane (fan-out per user) -----
    try:
        all_subs = client.list_all_enabled_subscriptions("crypto")
    except Exception:
        all_subs = []

    # Group subs by user
    subs_by_user: dict[str, list[dict]] = {}
    for sub in all_subs:
        user_id = sub.get("user_id") or "anonymous"
        subs_by_user.setdefault(user_id, []).append(sub)

    for user_id, user_subs in subs_by_user.items():
        try:
            alloc = client.get_allocation("crypto", user_id=user_id)
        except Exception:
            alloc = {"agent_pct": 0.70, "template_pct": 0.30}
        template_pct = float(alloc.get("template_pct", 0.30))
        if template_pct <= 0:
            continue
        total_weight = sum(float(s.get("weight", 1.0)) for s in user_subs) or 1.0
        user_lane_results: list[AgentState] = []
        for sub in user_subs:
            sub_weight = float(sub.get("weight", 1.0)) / total_weight
            sub_budget = template_pct * sub_weight
            try:
                state = await run_pipeline(
                    asset,
                    lane="user_template",
                    lane_budget_pct=sub_budget,
                    subscription_id=sub.get("id"),
                    template_id=sub.get("template_id"),
                    user_id=user_id,
                )
                results.append(state)
                user_lane_results.append(state)
            except Exception:
                logger.exception(
                    "user_template lane failed for user=%s sub=%s asset=%s",
                    user_id, sub.get("id"), asset,
                )
        # Collision check against agent_core for THIS user
        try:
            agent_core_state = results[0] if results and results[0].lane == "agent_core" else None
            if agent_core_state and user_lane_results:
                await _detect_and_publish_collisions(
                    asset, [agent_core_state, *user_lane_results], user_id=user_id,
                )
        except Exception:
            logger.exception("Collision detection failed for user=%s asset=%s", user_id, asset)

    return results


async def _detect_and_publish_collisions(
    asset: str, results: list[AgentState], user_id: str | None = None,
) -> None:
    """Emit NATS events when lanes agree (duplicate) or disagree (opposite)."""
    if len(results) < 2:
        return
    actionable = [r for r in results if r.action in ("BUY", "SELL")]
    if len(actionable) < 2:
        return
    by_lane: dict[str, list[str]] = {}
    for r in actionable:
        by_lane.setdefault(r.lane, []).append(r.action)

    # Only meaningful if agent_core and user_template both have actionable decisions
    agent_actions = set(by_lane.get("agent_core", []))
    tpl_actions = set(by_lane.get("user_template", []))
    if not agent_actions or not tpl_actions:
        return

    try:
        from app.services.publisher import publish_lane_event
    except Exception:
        return

    base_payload: dict = {"asset": asset}
    if user_id:
        base_payload["user_id"] = user_id

    if agent_actions == tpl_actions:
        await publish_lane_event("lane.signal_collision", {
            **base_payload,
            "direction": next(iter(agent_actions)),
            "lanes": ["agent_core", "user_template"],
        })
    elif (agent_actions | tpl_actions) == {"BUY", "SELL"}:
        await publish_lane_event("lane.opposite_collision", {
            **base_payload,
            "agent_action": next(iter(agent_actions)),
            "template_action": next(iter(tpl_actions)),
        })
