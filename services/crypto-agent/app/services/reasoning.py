"""
Reasoning engine: generates human-readable explanations from pipeline state.

This module transforms the raw AgentState into a structured reasoning string
that explains *why* the agent took a particular action. The reasoning is
stored alongside every decision for full auditability.
"""
from __future__ import annotations

from app.models.decision import AgentState, MemorySearchResult


def generate_reasoning(state: AgentState) -> str:
    """Build a multi-sentence reasoning paragraph from pipeline state."""
    parts: list[str] = []

    # Error short-circuit
    if state.error:
        parts.append(f"Pipeline error at step '{state.step}': {state.error}.")
        return " ".join(parts)

    # Signal information
    if state.signal is not None:
        score = state.signal.signal_score
        direction = state.signal.direction
        strength = _score_strength(abs(score))
        parts.append(
            f"Signal for {state.asset}: {strength} {direction.lower()} "
            f"(score {score:.4f}, threshold {state.signal.threshold:.4f})."
        )

        # Key signal drivers
        notable = _notable_components(state.signal.components)
        if notable:
            parts.append(f"Key drivers: {notable}.")
    else:
        parts.append(f"No signal data available for {state.asset}.")

    # Feature context
    if state.features:
        feature_summary = _summarise_features(state.features)
        if feature_summary:
            parts.append(feature_summary)

    # Strategy
    if state.strategy is not None:
        parts.append(
            f"Strategy '{state.strategy.name}' (v{state.strategy.version}) applied."
        )

        if state.strategy_weighted_score is not None and state.signal is not None:
            delta = state.strategy_weighted_score - state.signal.signal_score
            if abs(delta) > 0.001:
                direction_word = "increased" if delta > 0 else "decreased"
                parts.append(
                    f"Strategy weighting {direction_word} effective score "
                    f"from {state.signal.signal_score:.4f} to {state.strategy_weighted_score:.4f}."
                )

    # Memory insight
    if state.memories:
        parts.append(_memory_reasoning(state.memories, state.action))
    else:
        parts.append("No similar past episodes found in memory.")

    # Adjusted score
    if state.adjusted_score is not None and state.signal is not None:
        parts.append(
            f"Final adjusted score: {state.adjusted_score:.4f} "
            f"(original {state.signal.signal_score:.4f})."
        )

    # Action & risk
    action = state.action
    if action == "HOLD":
        parts.append("Signal below threshold; holding position with no trade.")
    elif action == "SKIP":
        reason = (
            state.risk_approval.reason if state.risk_approval else "unknown reason"
        )
        parts.append(f"Trade skipped: risk check rejected ({reason}).")
    else:
        # BUY or SELL
        if state.risk_approval:
            parts.append(
                f"Risk approved (level: {state.risk_approval.level}). "
                f"Executing {action} order."
            )

    # Order result
    if state.order_result is not None:
        mode = "shadow" if state.order_result.shadow_mode else "live"
        parts.append(
            f"Order submitted in {mode} mode (status: {state.order_result.reason})."
        )

    return " ".join(parts)


def _score_strength(abs_score: float) -> str:
    """Classify signal strength from the absolute score value."""
    if abs_score >= 0.8:
        return "very strong"
    if abs_score >= 0.6:
        return "strong"
    if abs_score >= 0.4:
        return "moderate"
    if abs_score >= 0.2:
        return "weak"
    return "very weak"


def _notable_components(components: dict[str, float], top_n: int = 3) -> str:
    """Return a comma-separated string of the top-N absolute-value components."""
    if not components:
        return ""
    sorted_items = sorted(components.items(), key=lambda kv: abs(kv[1]), reverse=True)
    top = sorted_items[:top_n]
    return ", ".join(f"{k} {'+' if v >= 0 else ''}{v:.3f}" for k, v in top)


def _summarise_features(features: dict) -> str:
    """Generate a short feature-context sentence."""
    parts: list[str] = []

    rsi = features.get("rsi_14") or features.get("rsi")
    if rsi is not None:
        if rsi < 30:
            parts.append(f"RSI oversold ({rsi:.0f})")
        elif rsi > 70:
            parts.append(f"RSI overbought ({rsi:.0f})")

    adx = features.get("adx")
    if adx is not None:
        if adx > 25:
            parts.append(f"strong trend (ADX {adx:.0f})")
        else:
            parts.append(f"weak trend (ADX {adx:.0f})")

    vol_ratio = features.get("volume_ratio") or features.get("volume_sma_ratio")
    if vol_ratio is not None and vol_ratio > 1.5:
        parts.append(f"elevated volume ({vol_ratio:.2f}x)")

    if not parts:
        return ""
    return "Feature context: " + ", ".join(parts) + "."


def _memory_reasoning(
    memories: list[MemorySearchResult],
    current_action: str | None = None,
) -> str:
    """Summarise what past episodes suggest."""
    if not memories:
        return "No similar past episodes found."

    total = len(memories)
    same_action_count = 0
    profitable_count = 0

    for m in memories:
        meta = m.record.metadata or {}
        if m.record.action == current_action:
            same_action_count += 1
        outcome = meta.get("outcome") or meta.get("profit")
        if outcome and (
            (isinstance(outcome, str) and outcome == "profitable")
            or (isinstance(outcome, (int, float)) and outcome > 0)
        ):
            profitable_count += 1

    parts = [f"{total} similar past episode(s) found."]
    if same_action_count:
        parts.append(f"{same_action_count} resulted in the same '{current_action}' action.")
    if profitable_count:
        parts.append(f"{profitable_count} were profitable.")

    return " ".join(parts)
