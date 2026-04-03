"""LLM 추론 엔진 — 유저별 OAuth 구독 기반.

유저가 등록한 Claude/Codex OAuth 토큰으로 LLM 호출.
토큰 미등록 시 데이터 기반 자동 추론 (deterministic but detailed).
"""
import logging
import math

from app.core.config import settings
from app.core.oauth import call_with_oauth, has_valid_token
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


def _smart_fallback(payload: ReasoningRequest) -> str:
    """데이터 기반 자동 추론 — LLM 없이도 의미 있는 분석 생성."""
    score = payload.signal_score
    components = payload.components or {}
    asset = payload.asset.replace("USDT", "")

    # Analyze signal strength
    abs_score = abs(score)
    if abs_score >= 0.6:
        strength = "강한"
    elif abs_score >= 0.3:
        strength = "중간 수준의"
    else:
        strength = "약한"

    direction = "상승" if score >= 0 else "하락"

    # Find dominant indicators
    sorted_comp = sorted(components.items(), key=lambda x: abs(x[1]), reverse=True)
    top_bullish = [(k, v) for k, v in sorted_comp if v > 0.1][:3]
    top_bearish = [(k, v) for k, v in sorted_comp if v < -0.1][:3]

    # Detect regime from components
    adx_filter = components.get("adx_filter", 1.0)
    rsi = components.get("rsi", 0)
    macd = components.get("macd", 0)
    bb = components.get("bollinger", 0)

    if adx_filter >= 1.1:
        regime_text = "강한 추세가 형성된 시장"
    elif adx_filter <= 0.6:
        regime_text = "뚜렷한 방향성이 없는 횡보 시장"
    else:
        regime_text = "보통 수준의 추세 시장"

    # Build reasoning sentences
    parts = []

    # Sentence 1: Overall direction and strength
    if abs_score < 0.15:
        parts.append(f"{asset}은(는) 현재 {regime_text}으로, 매매 시그널이 뚜렷하지 않아 관망이 적절합니다.")
    else:
        parts.append(f"{asset}은(는) {strength} {direction} 시그널을 보이고 있습니다 (점수: {score:.2f}).")

    # Sentence 2: Key indicators supporting the view
    if top_bullish and score >= 0:
        indicators = ", ".join(f"{k}({v:+.2f})" for k, v in top_bullish)
        parts.append(f"주요 상승 지표: {indicators}.")
    elif top_bearish and score < 0:
        indicators = ", ".join(f"{k}({v:+.2f})" for k, v in top_bearish)
        parts.append(f"주요 하락 지표: {indicators}.")

    # Sentence 3: Regime context
    parts.append(f"시장 환경은 {regime_text}입니다.")

    # Sentence 4: Conflicting signals (if any)
    if top_bullish and top_bearish:
        conflict = ", ".join(k for k, _ in top_bearish[:2]) if score >= 0 else ", ".join(k for k, _ in top_bullish[:2])
        parts.append(f"다만 {conflict}에서 반대 신호가 감지되어 주의가 필요합니다.")

    # Sentence 5: Action recommendation
    if abs_score >= 0.3:
        action = "매수" if score >= 0 else "매도"
        parts.append(f"전략 '{payload.strategy_name}' 기준으로 {action} 진입을 고려할 수 있으나, 손절 설정이 필수입니다.")
    else:
        parts.append(f"시그널 강도가 약하므로 추가 확인 후 진입을 권장합니다.")

    # Add memory reference if available
    if payload.memory_count > 0:
        parts.append(f"과거 유사 상황 {payload.memory_count}건을 참조하여 판단했습니다.")

    return " ".join(parts)


def build_reasoning_text(payload: ReasoningRequest, user_id: str | None = None) -> ReasoningResponse:
    """Generate reasoning using user's OAuth-authenticated LLM."""
    if not settings.enable_llm:
        return ReasoningResponse(
            reasoning=_smart_fallback(payload),
            provider="auto-reasoning",
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(payload)},
    ]

    uid = user_id or "anonymous"

    # Try user's OAuth providers
    for provider in ("claude", "codex"):
        if has_valid_token(uid, provider):
            result = call_with_oauth(uid, provider, messages, max_tokens=settings.max_tokens)
            if result:
                return ReasoningResponse(reasoning=result, provider=f"{provider}/oauth")

    # Smart fallback — detailed data-driven reasoning
    return ReasoningResponse(
        reasoning=_smart_fallback(payload),
        provider="auto-reasoning",
    )
