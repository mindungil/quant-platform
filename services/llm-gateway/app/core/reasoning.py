"""LLM 추론 엔진 — 멀티 프로바이더 체인.

Provider 우선순위:
1. GitHub Copilot (OAuth 구독)
2. Claude Code CLI (인증된 세션)
3. API Key fallback (LiteLLM)
4. 결정적 템플릿 (모두 실패 시)
"""
import logging

from app.core.config import settings
from app.core.providers import call_llm, get_available_providers
from app.models.reasoning import ReasoningRequest, ReasoningResponse

logger = logging.getLogger("llm-gateway")

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


def _deterministic_fallback(payload: ReasoningRequest) -> str:
    sentiment = "상승" if payload.signal_score >= 0 else "하락"
    strongest = sorted(
        payload.components.items(), key=lambda x: abs(x[1]), reverse=True
    )[:3]
    comp_text = ", ".join(f"{k}={v:.2f}" for k, v in strongest) or "데이터 부족"
    return (
        f"{payload.asset} 시그널 {sentiment} (점수: {payload.signal_score:.4f}). "
        f"전략 '{payload.strategy_name}' 활성 중. "
        f"주요 지표: {comp_text}. "
        f"참조 메모리 {payload.memory_count}건."
    )


def build_reasoning_text(payload: ReasoningRequest) -> ReasoningResponse:
    if not settings.enable_llm:
        return ReasoningResponse(
            reasoning=_deterministic_fallback(payload),
            provider="deterministic-fallback",
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(payload)},
    ]

    reasoning, provider = call_llm(
        messages, model=settings.default_model, max_tokens=settings.max_tokens,
    )

    if reasoning:
        return ReasoningResponse(reasoning=reasoning, provider=provider)

    return ReasoningResponse(
        reasoning=_deterministic_fallback(payload),
        provider="deterministic-fallback",
    )
