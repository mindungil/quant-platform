from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from prometheus_client import Counter, Histogram

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
from shared.regime import detect_regime, suggest_formula_type
from app.core.formula_selector import rank_formulas_ml
from shared.formulas import formula_registry
import shared.formulas.momentum
import shared.formulas.reversion
import shared.formulas.breakout
import shared.formulas.composite

agent_decisions_total = Counter(
    "agent_decisions_total",
    "Total agent decisions",
    ["action", "threshold_crossed"],
)
agent_decision_latency_seconds = Histogram(
    "agent_decision_latency_seconds",
    "Total decision loop latency",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

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


def _fetch_portfolio_balance(user_id: str) -> float:
    """Fetch total exposure (balance proxy) from portfolio-service. Falls back to default."""
    try:
        import httpx

        url = f"{settings.portfolio_service_base_url}/portfolio/{user_id}"
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        total_exposure = float(data.get("total_exposure", 0.0))
        if total_exposure > 0:
            return total_exposure
    except Exception as exc:
        logger.warning("portfolio_balance_fetch_failed", extra={"error": str(exc), "user_id": user_id})
    # Fallback: use default_max_notional as a rough capital estimate
    return settings.default_max_notional


def _calculate_position_size(
    signal_score: float,
    portfolio_balance: float,
    win_rate: float = 0.55,
    payoff_ratio: float = 1.5,
    realized_vol: float = 0.0,
    recent_returns: list[float] | None = None,
) -> float:
    """Position sizing with Kelly Criterion + scipy optimization.

    Two modes:
    1. If recent_returns provided (30+): use scipy to optimize Kelly from actual return distribution
    2. Otherwise: use analytical Kelly formula f* = (p*b - q) / b

    Always applies fractional Kelly and volatility adjustment.
    """
    kelly_optimal = 0.0

    # Mode 1: scipy-optimized Kelly from return distribution
    if recent_returns and len(recent_returns) >= 30:
        try:
            import numpy as np
            from scipy.optimize import minimize_scalar

            returns = np.array(recent_returns)

            def neg_growth_rate(fraction: float) -> float:
                """Negative geometric growth rate — we minimize this."""
                if fraction <= 0:
                    return 0.0
                growth = np.mean(np.log(1 + fraction * returns))
                return -growth

            result = minimize_scalar(neg_growth_rate, bounds=(0.001, 0.5), method="bounded")
            if result.success:
                kelly_optimal = result.x
        except Exception:
            pass

    # Mode 2: Analytical Kelly fallback
    if kelly_optimal <= 0:
        p = max(0.01, min(0.99, win_rate))
        q = 1 - p
        b = max(0.01, payoff_ratio)
        kelly_optimal = (p * b - q) / b

    # If Kelly is negative, no position
    if kelly_optimal <= 0:
        return 0.0

    # Fractional Kelly for safety
    risk_fraction = kelly_optimal * settings.kelly_fraction

    # Scale by signal strength
    signal_multiplier = min(abs(signal_score) / 0.6, 1.0)
    risk_fraction *= signal_multiplier

    # Volatility adjustment
    if realized_vol > 0 and settings.target_vol > 0:
        vol_scalar = settings.target_vol / max(realized_vol, 0.01)
        vol_scalar = min(vol_scalar, 1.5)
        risk_fraction *= vol_scalar

    # Cap at max_position_pct
    risk_fraction = min(risk_fraction, settings.max_position_pct)

    notional = portfolio_balance * risk_fraction
    return notional


def _build_order_request(decision: DecisionRecord) -> dict | None:
    """Build order request with Kelly-fraction position sizing.

    Returns None if the calculated notional is below min_order_notional.
    """
    reference_price = decision.reference_price or 0.0
    portfolio_balance = _fetch_portfolio_balance(decision.user_id)

    # Fetch per-strategy stats for Kelly inputs
    win_rate = 0.55  # default
    payoff_ratio = 1.5  # default
    realized_vol = 0.0
    try:
        import httpx
        stats_url = f"http://localhost:8013/statistics/{decision.user_id}"
        resp = httpx.get(stats_url, timeout=3.0)
        if resp.status_code == 200:
            stats = resp.json()
            if stats.get("win_rate", 0) > 0 and stats.get("trade_count", 0) >= 30:
                win_rate = stats["win_rate"]
            if stats.get("payoff_ratio", 0) > 0 and stats.get("trade_count", 0) >= 30:
                payoff_ratio = stats["payoff_ratio"]
    except Exception:
        pass

    requested_notional = _calculate_position_size(
        decision.signal_score, portfolio_balance, win_rate, payoff_ratio, realized_vol
    )

    # Skip if below minimum
    if requested_notional < settings.min_order_notional:
        logger.info(
            "order_below_minimum",
            extra={
                "requested_notional": requested_notional,
                "min_order_notional": settings.min_order_notional,
                "asset": decision.asset,
            },
        )
        return None

    # Cap at max_notional hard limit
    requested_notional = min(requested_notional, settings.default_max_notional)

    quantity = round(requested_notional / reference_price, 6) if reference_price > 0 else 0.01
    return {
        "user_id": decision.user_id,
        "exchange": settings.default_exchange,
        "asset": decision.asset,
        "side": decision.action,
        "quantity": quantity,
        "price": reference_price,
        "requested_notional": round(requested_notional, 2),
        "max_notional": settings.default_max_notional,
        "current_drawdown": settings.default_current_drawdown,
        "current_exposure": settings.default_current_exposure,
        "exposure_limit": settings.default_exposure_limit,
        "automation_enabled": settings.default_automation_enabled,
        "shadow_mode": True,
        "strategy_id": decision.strategy_id,
        "strategy_status": "ACTIVE",
        "correlation_id": decision.correlation_id,
        "stop_loss_pct": settings.default_stop_loss_pct,
        "take_profit_pct": settings.default_take_profit_pct,
        "trailing_stop_pct": settings.default_trailing_stop_pct,
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
# 8-phase adaptive decision loop
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


def _phase_detect(
    signal: SignalSnapshot,
    phases: list[PhaseResult],
) -> dict:
    """Phase 2 — Detect: classify market regime from signal features."""
    phase = _track_phase("detect")

    # Build features dict from signal snapshot
    features = {}
    for field_name in ("close", "volume", "rsi_14", "macd", "macd_signal",
                        "bb_upper", "bb_lower", "ema_9", "ema_21", "ema_50",
                        "sma_20", "atr_14", "adx_14", "stochastic_k", "stochastic_d", "vwap"):
        val = getattr(signal, field_name, None)
        if val is not None:
            features[field_name] = val

    regime = detect_regime(features)
    suggested_type = suggest_formula_type(regime)

    _complete_phase(phase, detail=f"regime={regime.label} suggest={suggested_type}")
    phases.append(phase)
    return {"regime": regime, "suggested_type": suggested_type, "features": features}


def _phase_recall(
    asset: str,
    regime_label: str,
    user_id: str,
    phases: list[PhaseResult],
) -> dict:
    """Phase 3 — Recall: query memory for best formula in this regime."""
    phase = _track_phase("recall")

    # Query memory for formula outcomes in similar regimes
    formula_rankings = {}
    try:
        import httpx
        resp = httpx.post(
            f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
            json={"regime_label": regime_label, "asset": asset, "top_k": 20},
            headers={"X-User-ID": user_id},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("items", []):
                record = item.get("record", {})
                fname = record.get("formula_name")
                outcome = record.get("trade_outcome")
                if fname and outcome is not None:
                    formula_rankings.setdefault(fname, []).append(outcome)
    except Exception as exc:
        logger.warning("formula_recall_failed", extra={"error": str(exc)})

    # Compute composite ranking per formula:
    #   score = mean_outcome * confidence_from_sample_size * recency_weight
    #
    # - mean_outcome: average PnL (higher = better)
    # - sample_confidence: sqrt(n) / sqrt(30) — reaches 1.0 at 30 samples
    # - regime_match_quality: average search score from memory (0-1)
    import math as _math

    formula_scores: dict[str, dict] = {}
    for fname, outcomes in formula_rankings.items():
        n = len(outcomes)
        mean_outcome = sum(outcomes) / n
        # Sample confidence: penalize formulas with few data points
        sample_confidence = min(_math.sqrt(n) / _math.sqrt(30), 1.0)
        # Risk-adjusted: penalize high variance (prefer consistent formulas)
        if n > 1:
            variance = sum((o - mean_outcome) ** 2 for o in outcomes) / (n - 1)
            std_dev = _math.sqrt(variance) if variance > 0 else 0.001
            risk_adjusted = mean_outcome / max(std_dev, 0.001)  # like Sharpe
        else:
            risk_adjusted = mean_outcome * 10  # single sample: scale by 10
        # Composite score
        composite = risk_adjusted * sample_confidence
        formula_scores[fname] = {
            "composite": composite,
            "mean_outcome": mean_outcome,
            "sample_count": n,
            "sample_confidence": round(sample_confidence, 3),
        }

    _complete_phase(phase, detail=f"formulas_found={len(formula_scores)}")
    phases.append(phase)
    return formula_scores


def _phase_score(
    features: dict,
    formula_scores: dict,
    suggested_type: str,
    phases: list[PhaseResult],
) -> tuple:
    """Phase 5 — Score: select and run the best formula.

    Selection priority:
    1. ML-based: GradientBoosting prediction (if >= 50 historical samples)
    2. Memory-based: formula with highest composite score (risk-adjusted * sample confidence)
    3. Regime-suggested: best formula type for detected market regime
    4. Default: composite_adaptive fallback
    """
    phase = _track_phase("score")

    selected_formula = None

    # Priority 1: ML-based selection (if enough historical data)
    if formula_scores:
        try:
            # Fetch full memory items for ML training
            import httpx
            resp = httpx.post(
                f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
                json={"regime_label": "", "top_k": 200},  # get all formula memories
                timeout=5.0,
            )
            if resp.status_code == 200:
                all_items = resp.json().get("items", [])
                ml_rankings = rank_formulas_ml(
                    features,
                    all_items,
                    formula_registry.list_names(),
                )
                if ml_rankings:
                    best_ml = ml_rankings[0]
                    selected_formula = formula_registry.get(best_ml.name)
                    if selected_formula:
                        logger.info(
                            "formula_selected_by_ml",
                            extra={
                                "formula": best_ml.name,
                                "predicted_score": best_ml.predicted_score,
                                "confidence": best_ml.confidence,
                            },
                        )
        except Exception as exc:
            logger.warning("ml_selection_failed", extra={"error": str(exc)})

    # Priority 2: Memory heuristic (risk-adjusted composite score)
    if selected_formula is None and formula_scores:
        best_name = max(formula_scores, key=lambda f: formula_scores[f]["composite"])
        best_info = formula_scores[best_name]
        if best_info["composite"] > 0:
            selected_formula = formula_registry.get(best_name)
            if selected_formula:
                logger.info(
                    "formula_selected_from_memory",
                    extra={
                        "formula": best_name,
                        "composite_score": round(best_info["composite"], 4),
                        "mean_outcome": round(best_info["mean_outcome"], 4),
                        "sample_count": best_info["sample_count"],
                    },
                )

    # If no memory or formula not found, use regime-suggested formula
    if selected_formula is None:
        candidates = formula_registry.get_for_regime(suggested_type)
        if candidates:
            selected_formula = candidates[0]
        else:
            selected_formula = formula_registry.get_default()

    # Run the formula
    result = selected_formula.compute(features)

    _complete_phase(phase, detail=f"formula={selected_formula.name} score={result.score:.4f} conf={result.confidence:.2f}")
    phases.append(phase)
    return selected_formula, result


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
        order_request = _build_order_request(decision)
        if order_request is not None:
            publisher.publish_agent_action(decision, order_request)

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
    """Execute the full 8-phase adaptive decision loop:
    gather -> detect -> recall -> select -> score -> retrieve -> check -> execute (+record).
    """
    _loop_start = time.monotonic()
    phases: list[PhaseResult] = []

    # Phase 1 — Gather
    signal = _phase_gather(asset, user_id, phases)

    # Phase 2 — Detect market regime (NEW)
    detection = _phase_detect(signal, phases)
    regime = detection["regime"]

    # Phase 3 — Recall formula performance from memory (NEW)
    formula_outcomes = _phase_recall(
        asset, regime.label, user_id or "bootstrap", phases
    )

    # Phase 4 — Select strategy (existing, renamed from phase 3)
    strategy, effective_user_id, action = _phase_select(asset, signal, user_id, phases)

    # Phase 5 — Score with adaptive formula (NEW)
    selected_formula, formula_result = _phase_score(
        detection["features"], formula_outcomes, detection["suggested_type"], phases
    )

    # Override signal score with formula result if formula has high confidence
    if formula_result.confidence >= 0.3:
        # Use formula score instead of fixed signal score
        signal_score = formula_result.score
        # Re-determine action based on formula score
        pos_threshold = abs(strategy.thresholds.get("entry", 0.6) if isinstance(strategy.thresholds, dict) else 0.6)
        neg_threshold = pos_threshold
        if signal_score >= pos_threshold:
            action = "BUY"
            threshold_crossed = True
        elif signal_score <= -neg_threshold:
            action = "SELL"
            threshold_crossed = True
        else:
            action = "HOLD"
            threshold_crossed = False
    else:
        signal_score = signal.signal_score
        threshold_crossed = signal.threshold_crossed

    # Phase 6 — Retrieve memory (existing)
    memory_response = _phase_retrieve(effective_user_id, asset, signal, strategy, phases)

    # Phase 7 — Check (existing)
    _phase_check(strategy, signal, asset, action, phases)

    # Phase 8 — Execute (modified to include formula info)
    decision = _phase_execute(
        asset, signal, strategy, memory_response, action,
        effective_user_id, correlation_id, phases,
    )

    # Attach formula metadata to decision
    decision.signal_score = signal_score
    decision.threshold_crossed = threshold_crossed
    # Store formula info as numeric confidence in components, text in reasoning
    decision.components = decision.components or {}
    decision.components["formula_confidence"] = round(formula_result.confidence, 4)
    decision.reasoning = (
        f"[formula={selected_formula.name} regime={regime.label}] "
        + (decision.reasoning or "")
    )

    decision.decision_phases = phases

    # Phase 9 — Record (existing)
    _phase_record(asset, decision, phases)

    # Record metrics
    agent_decisions_total.labels(
        action=decision.action,
        threshold_crossed=str(decision.threshold_crossed).lower(),
    ).inc()
    agent_decision_latency_seconds.observe(time.monotonic() - _loop_start)

    return decision
