"""LLM 추론 엔진 — 공식 API 기반.

LiteLLM을 통해 공식 LLM API를 호출합니다 (OpenAI, Anthropic 등).
환경변수에 API 키가 있으면 자동 활성화, 없으면 결정적 템플릿 fallback.
"""
import logging
import os

from app.core.config import settings
from app.models.reasoning import ReasoningRequest, ReasoningResponse

logger = logging.getLogger("llm-gateway")

# Set API keys for LiteLLM
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
    return f"""자산: {payload.asset}
시그널 점수: {payload.signal_score:.4f} ({direction})
활성 전략: {payload.strategy_name}
참조 메모리: {payload.memory_count}건
기술 지표:
{components_text or "  (데이터 없음)"}

현재 시장 상황을 분석하고, 매매 판단 근거를 3~5문장으로 설명하세요."""


def _call_llm(payload: ReasoningRequest) -> tuple[str | None, str]:
    """Call LLM via LiteLLM (공식 API)."""
    if not settings.enable_llm or not (settings.openai_api_key or settings.anthropic_api_key):
        return None, "no_api_key"

    try:
        import litellm
        response = litellm.completion(
            model=settings.default_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(payload)},
            ],
            max_tokens=settings.max_tokens,
            temperature=0.3,
        )
        content = response.choices[0].message.content
        if content:
            model_used = getattr(response, "model", settings.default_model)
            return content.strip(), f"litellm/{model_used}"
    except Exception as exc:
        logger.warning("llm_call_failed", extra={"error": str(exc)[:200]})
    return None, "error"


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
    """Generate reasoning — 공식 LLM API first, fallback to template."""
    reasoning, provider = _call_llm(payload)
    if reasoning:
        return ReasoningResponse(reasoning=reasoning, provider=provider)

    return ReasoningResponse(
        reasoning=_deterministic_fallback(payload),
        provider="deterministic-fallback",
    )
