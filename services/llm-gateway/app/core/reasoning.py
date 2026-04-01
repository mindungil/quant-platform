from app.core.config import settings
from app.models.reasoning import ReasoningRequest, ReasoningResponse


def build_reasoning_text(payload: ReasoningRequest) -> ReasoningResponse:
    sentiment = "bullish" if payload.signal_score >= 0 else "bearish"
    strongest_components = sorted(
        payload.components.items(),
        key=lambda item: abs(item[1]),
        reverse=True,
    )[:3]
    component_text = ", ".join(f"{name}={value:.2f}" for name, value in strongest_components) or "no strong components"

    external_text = "external context unavailable"
    if payload.external_context:
        external_text = ", ".join(
            f"{key}={value:.2f}" for key, value in payload.external_context.items()
        )

    reasoning = (
        f"{payload.asset} is {sentiment} with score {payload.signal_score:.4f}. "
        f"Strategy '{payload.strategy_name}' is active. "
        f"Top components: {component_text}. "
        f"External context: {external_text}. "
        f"Referenced {payload.memory_count} memory items."
    )
    return ReasoningResponse(reasoning=reasoning, provider=settings.provider_name)
