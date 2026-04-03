"""LLM-powered reasoning engine with intelligent routing.

Uses OpenClaw-style model routing:
- Simple queries → cheap models (gpt-4o-mini)
- Complex analysis → premium models (gpt-4o, claude-sonnet)
- Math/optimization → reasoning models (o3-mini)

Falls back to deterministic template if all LLM calls fail.
"""
import logging
import os

from app.core.config import settings
from app.core.router import classify_complexity, route_and_call
from app.models.reasoning import ReasoningRequest, ReasoningResponse

logger = logging.getLogger("llm-gateway")

# Set API keys in environment for LiteLLM
if settings.openai_api_key:
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
if settings.anthropic_api_key:
    os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)


SYSTEM_PROMPT = """당신은 퀀트 트레이딩 AI 에이전트의 추론 엔진입니다.
시장 데이터와 기술 지표를 분석하여 매매 판단의 근거를 한국어로 설명합니다.

규칙:
- 3~5문장으로 간결하게 분석
- 핵심 지표 수치를 언급
- 현재 시장 레짐(추세/횡보/변동성)을 판단
- 매매 방향(매수/매도/관망)의 근거를 명확히
- 리스크 요인도 한 줄 언급"""


def _build_user_prompt(payload: ReasoningRequest) -> str:
    direction = "상승(bullish)" if payload.signal_score >= 0 else "하락(bearish)"

    components_text = ""
    if payload.components:
        sorted_comp = sorted(payload.components.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
        components_text = "\n".join(f"  - {k}: {v:.4f}" for k, v in sorted_comp)

    external_text = ""
    if payload.external_context:
        external_text = "\n".join(f"  - {k}: {v:.2f}" for k, v in payload.external_context.items())

    return f"""자산: {payload.asset}
시그널 점수: {payload.signal_score:.4f} ({direction})
활성 전략: {payload.strategy_name}
참조 메모리: {payload.memory_count}건

기술 지표:
{components_text or "  (데이터 없음)"}

외부 컨텍스트:
{external_text or "  (없음)"}

위 데이터를 바탕으로 현재 시장 상황을 분석하고, 매매 판단 근거를 설명하세요."""


def _deterministic_fallback(payload: ReasoningRequest) -> str:
    sentiment = "상승" if payload.signal_score >= 0 else "하락"
    strongest = sorted(
        payload.components.items(),
        key=lambda item: abs(item[1]),
        reverse=True,
    )[:3]
    comp_text = ", ".join(f"{k}={v:.2f}" for k, v in strongest) or "데이터 부족"

    return (
        f"{payload.asset} 시그널 {sentiment} (점수: {payload.signal_score:.4f}). "
        f"전략 '{payload.strategy_name}' 활성 중. "
        f"주요 지표: {comp_text}. "
        f"참조 메모리 {payload.memory_count}건."
    )


def build_reasoning_text(payload: ReasoningRequest) -> ReasoningResponse:
    """Generate reasoning with intelligent model routing."""
    if not settings.enable_llm:
        return ReasoningResponse(
            reasoning=_deterministic_fallback(payload),
            provider="deterministic-fallback",
        )

    user_prompt = _build_user_prompt(payload)
    tier = classify_complexity(user_prompt, payload.components)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    reasoning, model_used = route_and_call(
        messages=messages,
        tier=tier,
        prompt_text=user_prompt,
        components=payload.components,
        max_tokens=settings.max_tokens,
        temperature=0.3,
    )

    if reasoning:
        return ReasoningResponse(
            reasoning=reasoning,
            provider=f"litellm/{model_used} (tier={tier})",
        )

    # All LLM calls failed — use fallback
    return ReasoningResponse(
        reasoning=_deterministic_fallback(payload),
        provider="deterministic-fallback",
    )
