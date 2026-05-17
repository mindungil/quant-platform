from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

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
try:
    from app.core.mab_state import formula_mab
except ImportError:
    formula_mab = None  # public-only build (bandit / mab_state are private IP)
try:
    from app.core.formula_selector import rank_formulas_ml
except ImportError:
    rank_formulas_ml = None  # public-only build (formula_selector is private IP)
try:
    from shared.formulas import formula_registry
except ImportError:
    formula_registry = None  # type: ignore
try:
    import shared.formulas.momentum
    import shared.formulas.reversion
    import shared.formulas.breakout
    import shared.formulas.composite
except ImportError:
    pass  # formulas pkg is private — registry stays empty

agent_escalations_total = Counter(
    "agent_escalations_total",
    "Total escalations to deep reasoning",
    ["reason"],
)

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
agent_phase_total = Counter(
    "agent_phase_total",
    "Counters per decision phase",
    ["phase", "status"],
)
decision_phase_total = Counter(
    "decision_phase_total",
    "Decision phase execution count",
    ["phase"],
)
agent_decision_outcomes_total = Counter(
    "agent_decision_outcomes_total",
    "Decision outcomes by action",
    ["action"],
)
decision_outcome_total = Counter(
    "decision_outcome_total",
    "Decision outcomes by buy/sell/hold",
    ["outcome"],
)
stale_signal_skipped_total = Counter(
    "stale_signal_skipped_total",
    "Total signals skipped due to staleness",
)

signal_client = SignalClient(settings.signal_service_base_url)
memory_client = MemoryClient(settings.memory_service_base_url)
strategy_client = StrategyClient(settings.strategy_registry_base_url)
llm_gateway_client = LlmGatewayClient(settings.llm_gateway_base_url)
realtime_bus = RealtimeBus(RedisStore(settings.redis_url))
logger = get_logger("crypto-agent")

# LangGraph StateGraph — imported AFTER clients so graph.py can capture them
from app.core.graph import agent_graph  # noqa: E402
from app.core.graph_state import AgentState  # noqa: E402

# FormulaMAB is imported from app.core.mab_state (shared with outcome_consumer)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIGNAL_STALENESS_SECONDS = int(os.environ.get("SIGNAL_STALENESS_SECONDS", "300"))  # 5 minutes default
DUPLICATE_WINDOW_SECONDS = int(os.environ.get("DUPLICATE_WINDOW_SECONDS", "60"))


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
    agent_phase_total.labels(phase=phase.name, status="completed").inc()
    decision_phase_total.labels(phase=phase.name).inc()
    return phase


def _fail_phase(phase: PhaseResult, *, detail: str | None = None) -> PhaseResult:
    now = datetime.now(UTC)
    phase.status = "failed"
    phase.ended_at = now
    if phase.started_at:
        phase.duration_ms = round((now - phase.started_at).total_seconds() * 1000, 2)
    phase.detail = detail
    agent_phase_total.labels(phase=phase.name, status="failed").inc()
    return phase


# ---------------------------------------------------------------------------
# Helpers (unchanged logic)
# ---------------------------------------------------------------------------

def _should_escalate(state: dict) -> bool:
    """ULTRAPLAN-style escalation check.

    Returns True if:
    - risk_issues exist AND action is not HOLD (risk denied but agent wants to act)
    - formula_score is within 0.05 of the entry threshold (borderline)
    - regime changed in last 3 decisions (rapid regime shifts)
    """
    risk_issues = state.get("risk_issues") or []
    action = state.get("action", "HOLD")

    # Condition 1: risk-denied but wanting to act
    if len(risk_issues) > 0 and action != "HOLD":
        agent_escalations_total.labels(reason="risk_conflict").inc()
        return True

    # Condition 2: borderline formula score
    formula_score = state.get("formula_score")
    if formula_score is not None:
        strategy = state.get("strategy") or {}
        thresholds = strategy.get("thresholds", {})
        entry_threshold = abs(
            thresholds.get("entry", 0.6) if isinstance(thresholds, dict) else 0.6
        )
        distance = abs(abs(formula_score) - entry_threshold)
        if distance <= 0.05:
            agent_escalations_total.labels(reason="borderline_score").inc()
            return True

    # Condition 3: regime changed recently (check via memory)
    asset = state.get("asset", "")
    user_id = state.get("effective_user_id") or state.get("user_id") or "system"
    try:
        import httpx
        resp = httpx.post(
            f"{settings.memory_service_base_url}/memory/search",
            json={"user_id": user_id, "asset": asset, "signal_score": 0.0, "top_k": 3},
            headers={"X-User-ID": user_id},
            timeout=3.0,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            regimes = [
                item.get("record", {}).get("regime_label")
                for item in items
                if item.get("record", {}).get("regime_label")
            ]
            if len(regimes) >= 2 and len(set(regimes)) >= 2:
                agent_escalations_total.labels(reason="regime_shift").inc()
                return True
    except Exception:
        pass

    return False


def _escalate_to_deep_reasoning(state: dict, asset: str) -> str:
    """Call llm_gateway with full context from all 8 phases for enriched reasoning."""
    context = {
        "asset": asset,
        "signal": state.get("signal"),
        "regime": state.get("regime"),
        "formula_scores": state.get("formula_scores"),
        "selected_formula": state.get("selected_formula"),
        "formula_score": state.get("formula_score"),
        "formula_confidence": state.get("formula_confidence"),
        "risk_issues": state.get("risk_issues"),
        "action": state.get("action"),
        "threshold_crossed": state.get("threshold_crossed"),
        "features": state.get("features"),
        "mab_stats": state.get("mab_stats"),
        "strategy": state.get("strategy"),
    }
    try:
        import httpx
        resp = httpx.post(
            f"{settings.llm_gateway_base_url}/reasoning/generate",
            json={
                "asset": asset,
                "signal_score": state.get("formula_score", 0.0),
                "strategy_name": (state.get("strategy") or {}).get("name", "unknown"),
                "memory_count": 0,
                "components": context,
                "deep_reasoning": True,
            },
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json().get("reasoning", "")
    except Exception as exc:
        logger.warning("deep_reasoning_failed", extra={"error": str(exc)[:100]})

    # Fallback: build a structured summary
    return (
        f"[ESCALATED] {asset} | action={state.get('action')} "
        f"formula_score={state.get('formula_score', 0):.4f} "
        f"regime={state.get('regime')} "
        f"risk_issues={state.get('risk_issues')} "
        f"confidence={state.get('formula_confidence', 0):.2f}"
    )


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
    win_rate: float = 0.0,
    payoff_ratio: float = 0.0,
    realized_vol: float = 0.0,
    recent_returns: list[float] | None = None,
) -> float:
    """Position sizing: Kelly Criterion + volatility targeting + signal scaling.

    Priority:
    1. scipy-optimized Kelly from actual return distribution (30+ samples)
    2. Analytical Kelly from win_rate/payoff_ratio (if provided and valid)
    3. Conservative fixed fraction fallback (1% of portfolio)

    All results go through fractional Kelly, signal scaling, vol adjustment.
    """
    import numpy as np

    kelly_optimal = 0.0

    # Mode 1: scipy-optimized Kelly from return distribution
    if recent_returns and len(recent_returns) >= 30:
        try:
            from scipy.optimize import minimize_scalar
            returns = np.array(recent_returns)

            # Compute win_rate and payoff from actual data if not provided
            if win_rate <= 0:
                wins = returns[returns > 0]
                losses = returns[returns <= 0]
                win_rate = len(wins) / max(len(returns), 1)
                if len(wins) > 0 and len(losses) > 0 and np.mean(np.abs(losses)) > 0:
                    payoff_ratio = np.mean(wins) / np.mean(np.abs(losses))

            # Autocorrelation adjustment — reduce Kelly if returns are autocorrelated
            ac_adj = 1.0
            if len(returns) >= 50:
                lag1 = np.corrcoef(returns[:-1], returns[1:])[0, 1]
                if not np.isnan(lag1):
                    ac_adj = max(0.5, 1.0 - abs(lag1))

            def neg_growth_rate(fraction: float) -> float:
                if fraction <= 0:
                    return 0.0
                growth = np.mean(np.log(np.maximum(1 + fraction * returns, 1e-10)))
                return -growth

            result = minimize_scalar(neg_growth_rate, bounds=(0.001, 0.5), method="bounded")
            if result.success and result.x > 0:
                kelly_optimal = result.x * ac_adj
        except Exception as exc:
            logger.debug("scipy_kelly_optimization_failed", extra={"error": str(exc)[:100]})

    # Mode 2: Analytical Kelly (only if valid win_rate/payoff)
    if kelly_optimal <= 0 and win_rate > 0.1 and payoff_ratio > 0.1:
        p = max(0.01, min(0.99, win_rate))
        q = 1 - p
        b = max(0.01, payoff_ratio)
        kelly_raw = (p * b - q) / b
        # Cap analytical Kelly at 25% (can be unreliable without data)
        kelly_optimal = min(max(kelly_raw, 0.0), 0.25)

    # Mode 3: Conservative fallback
    if kelly_optimal <= 0:
        kelly_optimal = 0.02  # 2% fixed fraction

    # Fractional Kelly for safety (half-Kelly standard)
    risk_fraction = kelly_optimal * settings.kelly_fraction

    # Signal strength scaling — sigmoid-like curve instead of linear
    abs_signal = abs(signal_score)
    signal_multiplier = 1.0 / (1.0 + np.exp(-10 * (abs_signal - 0.4)))  # steep sigmoid around 0.4
    risk_fraction *= max(signal_multiplier, 0.1)  # minimum 10% of position

    # Volatility targeting — scale position inversely to realized vol
    if realized_vol > 0 and settings.target_vol > 0:
        vol_scalar = settings.target_vol / max(realized_vol, 0.005)
        vol_scalar = max(0.3, min(vol_scalar, 2.0))  # clamp between 0.3x and 2x
        risk_fraction *= vol_scalar

    # CVaR cap (Rockafellar & Uryasev 2000) — bound expected tail loss
    # Skip if we don't know vol; otherwise enforce 95% CVaR ≤ 2% of equity.
    if realized_vol > 0:
        try:
            from shared.risk.position_sizing import cvar_cap_scaler
            # Treat realized_vol as per-period sigma. Edge proxy from signal.
            edge_proxy = max(0.0, abs(signal_score) * 0.01)
            cvar_scale = cvar_cap_scaler(
                sigma_per_period=realized_vol,
                mean_per_period=edge_proxy,
                cap_pct=0.02,
                confidence=0.95,
            )
            risk_fraction *= cvar_scale
        except Exception as exc:
            logger.debug("cvar_cap_skipped", extra={"error": str(exc)[:100]})

    # Hard caps
    risk_fraction = min(risk_fraction, settings.max_position_pct)

    notional = portfolio_balance * risk_fraction
    return notional


def _build_order_request(decision: DecisionRecord, *, shadow_override: bool = False) -> dict | None:
    """Build order request with Kelly-fraction position sizing.

    When *shadow_override* is True the order is forced into shadow_mode
    regardless of the global default (used for SHADOW-status strategies).

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
        stats_url = f"{settings.statistics_service_base_url}/statistics/{decision.user_id}"
        resp = httpx.get(stats_url, timeout=3.0)
        if resp.status_code == 200:
            stats = resp.json()
            if stats.get("win_rate", 0) > 0 and stats.get("trade_count", 0) >= 30:
                win_rate = stats["win_rate"]
            if stats.get("payoff_ratio", 0) > 0 and stats.get("trade_count", 0) >= 30:
                payoff_ratio = stats["payoff_ratio"]
    except Exception as exc:
        logger.debug("stats_fetch_failed_for_kelly", extra={"error": str(exc)[:100]})

    # Check if strategy has backtest Kelly params (override defaults when live stats insufficient)
    if decision.strategy_id:
        try:
            import httpx
            strategy_resp = httpx.get(
                f"{settings.strategy_registry_base_url}/strategies/{decision.strategy_id}",
                timeout=3.0,
            )
            if strategy_resp.status_code == 200:
                strat_data = strategy_resp.json()
                br = strat_data.get("backtest_results") or {}
                if br.get("backtest_win_rate", 0) > 0:
                    # Use backtest params as baseline if live stats have insufficient data
                    if win_rate == 0.55:  # still at default
                        win_rate = br["backtest_win_rate"]
                    if payoff_ratio == 1.5:  # still at default
                        payoff_ratio = br.get("backtest_payoff_ratio", payoff_ratio)
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

    # V4-2 / V4-5 wiring: Capital tier cap (paper/micro/small/mid/full) and
    # risk-monitor-hub soft-throttle. Kill events from the hub force PAPER
    # via capital_tier.register_kill_from_risk_hub, so just reading the
    # tier-aware cap here covers both layers.
    try:
        from shared.risk import capital_tier
        from shared.risk.monitor_hub import current_size_multiplier
        soft_mult = current_size_multiplier(scope="crypto-agent")
        if soft_mult <= 0:
            logger.warning("order_throttled_to_zero", extra={"asset": decision.asset})
            return None
        requested_notional = capital_tier.cap_order_notional(requested_notional * soft_mult)
        if requested_notional < settings.min_order_notional:
            return None
    except ImportError:
        pass  # public-only build — V4 modules absent

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
        "shadow_mode": True if shadow_override else settings.default_shadow_mode,
        "strategy_id": decision.strategy_id,
        "strategy_status": decision.components.get("_strategy_status", "ACTIVE") if decision.components else "ACTIVE",
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

    # 1. Strategy must be ACTIVE or SHADOW
    strategy_status = getattr(strategy, "status", "ACTIVE")
    if strategy_status not in ("ACTIVE", "SHADOW"):
        issues.append(f"strategy status is '{strategy_status}', expected ACTIVE or SHADOW")

    # 2. Signal freshness — feature_timestamp within SIGNAL_STALENESS_SECONDS
    now = datetime.now(UTC)
    feature_ts = signal.feature_timestamp
    if feature_ts.tzinfo is None:
        # treat naive as UTC
        feature_ts = feature_ts.replace(tzinfo=UTC)
    staleness = (now - feature_ts).total_seconds()
    if staleness > SIGNAL_STALENESS_SECONDS:
        stale_signal_skipped_total.inc()
        logger.warning(
            "signal_stale_skipped",
            extra={"asset": asset, "staleness_seconds": round(staleness), "limit_seconds": SIGNAL_STALENESS_SECONDS},
        )
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
    indicator_fields = ("close", "volume", "rsi_14", "macd", "macd_signal",
                        "bb_upper", "bb_lower", "ema_9", "ema_21", "ema_50",
                        "sma_20", "atr_14", "adx_14", "stochastic_k", "stochastic_d", "vwap")
    for field_name in indicator_fields:
        val = getattr(signal, field_name, None)
        if val is not None:
            features[field_name] = val

    # Augment from feature-store: signal-service's response model drops the
    # raw indicators, so without this fallback formula.compute() sees None
    # for ema_9/atr_14/macd_signal/etc and returns confidence=0 for every
    # bar — every decision becomes HOLD, FormulaMAB never gets a reward,
    # the entire learning loop starves. See commit 3a39a87 / P1 plan.
    try:
        import httpx as _httpx
        fs_resp = _httpx.get(
            f"{settings.feature_store_base_url}/features/{signal.asset}/latest",
            timeout=3.0,
        )
        if fs_resp.status_code == 200:
            fs_payload = fs_resp.json()
            for field_name in indicator_fields:
                if features.get(field_name) is None and fs_payload.get(field_name) is not None:
                    features[field_name] = fs_payload[field_name]
    except Exception as exc:
        logger.debug("phase_detect_feature_store_skipped", extra={
            "asset": signal.asset, "error": str(exc)[:100],
        })

    regime = detect_regime(features, asset=signal.asset)
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
    2. Thompson Sampling MAB: contextual bandit (exploration-exploitation)
    3. Memory-based: formula with highest composite score (risk-adjusted * sample confidence)
    4. Regime-suggested: best formula type for detected market regime
    5. Default: composite_adaptive fallback
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

    # Priority 2: Thompson Sampling MAB (contextual bandit)
    if selected_formula is None and formula_scores and formula_mab is not None and formula_registry is not None:
        try:
            import httpx
            resp = httpx.post(
                f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
                json={"regime_label": suggested_type, "top_k": 200},
                timeout=5.0,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    formula_mab.load_from_memory(items)
        except Exception as exc:
            logger.debug("mab_memory_fetch_failed", extra={"error": str(exc)[:100]})

        mab_choice = formula_mab.select(
            regime=suggested_type,
            eligible=list(formula_scores.keys()),
        )
        selected_formula = formula_registry.get(mab_choice)
        if selected_formula:
            logger.info(
                "formula_selected_by_mab",
                extra={"formula": mab_choice, "regime": suggested_type},
            )

    # Priority 3: Memory heuristic (risk-adjusted composite score)
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

    # Priority 4: If no memory or formula not found, use regime-suggested formula
    if selected_formula is None:
        candidates = formula_registry.get_for_regime(suggested_type)
        if candidates:
            selected_formula = candidates[0]
        else:
            selected_formula = formula_registry.get_default()

    # Run the formula (style formula via MAB/ML)
    style_result = selected_formula.compute(features)

    # Run factor ensemble (primary scoring engine)
    try:
        from shared.formulas.factor_ensemble import FactorEnsembleFormula
        _ensemble = FactorEnsembleFormula()
        ensemble_result = _ensemble.compute(features)

        # Blend: ensemble 70% + style 30%
        from shared.formulas.base import FormulaResult
        blended_score = ensemble_result.score * 0.7 + style_result.score * 0.3
        blended_confidence = ensemble_result.confidence * 0.7 + style_result.confidence * 0.3
        blended_components = dict(ensemble_result.components)
        blended_components["ensemble_score"] = round(ensemble_result.score, 4)
        blended_components["style_score"] = round(style_result.score, 4)
        blended_components["style_formula"] = selected_formula.name
        blended_components["formula_confidence"] = round(blended_confidence, 4)

        result = FormulaResult(
            score=max(-1.0, min(1.0, blended_score)),
            confidence=min(blended_confidence, 1.0),
            components=blended_components,
            formula_name=f"ensemble+{selected_formula.name}",
            regime_label=ensemble_result.regime_label,
        )
        logger.info("ensemble_blend", extra={
            "ensemble": round(ensemble_result.score, 4),
            "style": round(style_result.score, 4),
            "blended": round(blended_score, 4),
            "style_formula": selected_formula.name,
        })
    except Exception as exc:
        logger.warning("ensemble_fallback", extra={"error": str(exc)[:200]})
        result = style_result  # fallback to style formula only

    _complete_phase(phase, detail=f"formula={result.formula_name} score={result.score:.4f} conf={result.confidence:.2f}")
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


def _fetch_shadow_strategies() -> list[dict]:
    """Fetch strategies in SHADOW status from strategy-registry."""
    try:
        import httpx
        resp = httpx.get(
            f"{settings.strategy_registry_base_url}/strategies/shadow",
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.debug("shadow_strategies_fetch_failed", extra={"error": str(exc)[:100]})
    return []


def _phase_select(
    asset: str,
    signal: SignalSnapshot,
    user_id: str | None,
    phases: list[PhaseResult],
) -> tuple[StrategySnapshot, str, str]:
    """Phase 3 — Select: load active strategy and determine effective user & action.

    Also considers SHADOW strategies for the same asset type. When a SHADOW
    strategy is selected, it will be run in shadow_mode (paper-trading) via
    the ``_is_shadow_strategy`` flag attached to the returned snapshot.
    """
    phase = _track_phase("select")

    # Primary: load ACTIVE strategy
    strategy = strategy_client.get_active_strategy(
        "crypto",
        user_id=user_id or getattr(signal, "strategy_user_id", None),
    )

    # Check for SHADOW strategies — they run alongside ACTIVE but in paper-trading mode
    is_shadow = False
    shadow_strategies = _fetch_shadow_strategies()
    for ss in shadow_strategies:
        if ss.get("asset_type") == "crypto":
            # Use the SHADOW strategy for this cycle (round-robin style)
            try:
                shadow_snap = StrategySnapshot.model_validate(ss)
                strategy = shadow_snap
                is_shadow = True
                break
            except Exception as exc:
                logger.debug("shadow_strategy_parse_failed", extra={"strategy_id": ss.get("id", "?"), "error": str(exc)[:80]})

    # Attach shadow flag as a dynamic attribute for downstream use
    strategy._is_shadow = is_shadow  # type: ignore[attr-defined]

    strategy_user_id = getattr(signal, "strategy_user_id", None) or strategy.user_id
    effective_user_id = user_id or strategy_user_id or "bootstrap"
    action = signal.direction
    status_label = "SHADOW" if is_shadow else "ACTIVE"
    _complete_phase(phase, detail=f"strategy={strategy.name} status={status_label} action={action}")
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
    except Exception as exc:
        logger.warning("llm_reasoning_failed_using_fallback", extra={"error": str(exc)[:100]})

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
        is_shadow = (decision.components or {}).get("_strategy_status") == "SHADOW"
        order_request = _build_order_request(decision, shadow_override=is_shadow)
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
    """Execute the full 8-phase adaptive decision loop via LangGraph StateGraph.

    Phases: gather -> detect -> recall -> select -> score -> check -> execute -> record
    """
    _loop_start = time.monotonic()

    # Build initial state
    initial_state: AgentState = {
        "asset": asset,
        "user_id": user_id,
        "correlation_id": correlation_id,
        "signal": None,
        "signal_age_seconds": None,
        "regime": None,
        "suggested_formula_type": None,
        "features": None,
        "formula_scores": None,
        "mab_stats": None,
        "strategy": None,
        "effective_user_id": user_id or "bootstrap",
        "is_shadow": False,
        "selected_formula": None,
        "formula_score": None,
        "formula_confidence": None,
        "formula_components": None,
        "risk_issues": [],
        "action": "HOLD",
        "threshold_crossed": False,
        "decision_id": None,
        "order_request": None,
        "order_submitted": False,
        "reasoning": None,
        "escalated": False,
        "recorded": False,
        "errors": [],
        "phase_timings": {},
        "phase_details": {},
        "abort": False,
    }

    # Invoke graph
    final_state = agent_graph.invoke(initial_state)

    # Reconstruct DecisionRecord from final state
    signal_dict = final_state.get("signal") or {}
    strategy_dict = final_state.get("strategy") or {}
    # Use ensemble components if available, fallback to signal components
    formula_components = final_state.get("formula_components")
    if formula_components and "ensemble_score" in formula_components:
        components = dict(formula_components)
    else:
        components = dict(signal_dict.get("components") or {})
    # Always merge in the score-node components if present so the daily
    # optimizer can attribute price moves back to factors. Without this the
    # optimizer sees only formula_confidence and never updates weights.
    if formula_components:
        for k, v in formula_components.items():
            if k not in components:
                components[k] = v
    components["formula_confidence"] = round(final_state.get("formula_confidence") or 0.0, 4)
    # Embed regime + style formula so hindsight + MAB can do contextual updates.
    if final_state.get("regime"):
        components["regime"] = final_state.get("regime")
    if final_state.get("selected_formula"):
        components["style_formula"] = final_state.get("selected_formula")

    decision_id = final_state.get("decision_id") or str(__import__("uuid").uuid4())
    decision = DecisionRecord(
        decision_id=decision_id,
        timestamp=datetime.now(UTC),
        user_id=final_state.get("effective_user_id", "bootstrap"),
        asset=asset,
        asset_type="crypto",
        signal_score=final_state.get("formula_score") or signal_dict.get("signal_score", 0.0),
        strategy_id=strategy_dict.get("id", "unknown"),
        strategy_name=strategy_dict.get("name", "unknown"),
        action=final_state.get("action", "HOLD"),
        threshold_crossed=final_state.get("threshold_crossed", False),
        reasoning=final_state.get("reasoning") or "",
        memory_refs=[],
        components=components,
        correlation_id=final_state.get("correlation_id") or decision_id,
        reference_price=signal_dict.get("reference_price"),
    )

    # Build phase results from timings
    phase_timings = final_state.get("phase_timings") or {}
    phase_details = final_state.get("phase_details") or {}
    phase_order = [
        ("gather", "gather"),
        ("detect", "detect"),
        ("retrieve", "recall"),
        ("select", "select"),
        ("score", "score"),
        ("check", "check"),
        ("execute", "execute"),
        ("record", "record"),
    ]
    phases: list[PhaseResult] = []
    for phase_name, source_name in phase_order:
        duration_ms = phase_timings.get(source_name)
        detail = phase_details.get(source_name)
        if duration_ms is None and detail is None:
            continue
        phases.append(PhaseResult(
            name=phase_name,
            status="completed",
            duration_ms=duration_ms if duration_ms is not None else 0.0,
            detail=detail,
        ))
    decision.decision_phases = phases

    # Record metrics
    decision_outcome_total.labels(outcome=decision.action.lower()).inc()
    agent_decision_outcomes_total.labels(action=decision.action).inc()
    agent_decisions_total.labels(
        action=decision.action,
        threshold_crossed=str(decision.threshold_crossed).lower(),
    ).inc()
    agent_decision_latency_seconds.observe(time.monotonic() - _loop_start)

    # Log errors if any
    graph_errors = final_state.get("errors") or []
    if graph_errors:
        logger.warning("graph_completed_with_errors", extra={
            "asset": asset, "errors": graph_errors[:5],
        })

    return decision
