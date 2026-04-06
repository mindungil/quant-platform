"""LLM 추론 엔진 — 유저별 OAuth 구독 기반 + API key fallback.

유저가 등록한 Claude/Codex OAuth 토큰으로 LLM 호출.
API key 환경변수(OPENAI_API_KEY, ANTHROPIC_API_KEY) 사용 가능.
토큰/키 미등록 시 데이터 기반 자동 추론 (deterministic but detailed).
"""
import json
import logging
import math
import os

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


def generate_structured_reasoning(
    asset: str,
    signal_score: float,
    strategy_name: str,
    memory_count: int,
    components: dict[str, float],
    regime: str | None,
    formula_name: str | None,
) -> str:
    """Generate rich structured reasoning text without LLM."""
    direction = "bullish" if signal_score >= 0 else "bearish"
    abs_score = abs(signal_score)
    strength = "strong" if abs_score > 0.7 else "moderate" if abs_score > 0.4 else "weak"

    top_drivers = sorted(components.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
    drivers_text = ", ".join(f"{k}={v:.2f}" for k, v in top_drivers) if top_drivers else "N/A"

    regime_desc = {
        "trending": "a trending market",
        "trending_up": "a bullish trending market",
        "trending_down": "a bearish trending market",
        "ranging": "a ranging/sideways market",
        "sideways": "a ranging/sideways market",
        "volatile": "a high-volatility environment",
        "volatile_up": "a volatile bullish environment",
        "volatile_down": "a volatile bearish environment",
    }.get((regime or "").split("_")[0] if regime else "", "current market conditions")
    if regime and regime in ("trending_up", "trending_down", "volatile_up", "volatile_down"):
        regime_desc = {
            "trending_up": "a bullish trending market",
            "trending_down": "a bearish trending market",
            "volatile_up": "a volatile bullish environment",
            "volatile_down": "a volatile bearish environment",
        }.get(regime, regime_desc)

    formula_part = f"Formula {formula_name} selected" if formula_name else "Default formula applied"

    return (
        f"Signal: {strength} {direction} ({signal_score:.3f}) in {regime_desc}. "
        f"{formula_part} based on {memory_count} historical references. "
        f"Key drivers: {drivers_text}. "
        f"Strategy {strategy_name} applied."
    )


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


def _generate_reasoning_structured(payload: ReasoningRequest) -> dict:
    """Generate structured reasoning data for rich frontend display."""
    score = payload.signal_score
    components = payload.components or {}
    asset = payload.asset.replace("USDT", "").replace("KRW-", "")

    abs_score = abs(score)

    # Signal strength
    if abs_score >= 0.6:
        strength = "강함"
        strength_level = 3
    elif abs_score >= 0.3:
        strength = "보통"
        strength_level = 2
    else:
        strength = "약함"
        strength_level = 1

    direction = "매수" if score >= 0 else "매도"
    action = "BUY" if score >= 0.6 else "SELL" if score <= -0.6 else "HOLD"

    # Regime
    adx = components.get("adx_filter", 1.0)
    if adx >= 1.1:
        regime = "추세"
        regime_desc = "강한 추세가 형성된 시장"
    elif adx <= 0.6:
        regime = "횡보"
        regime_desc = "뚜렷한 방향성이 없는 횡보 시장"
    else:
        regime = "중립"
        regime_desc = "보통 수준의 추세 시장"

    # Top indicators
    sorted_comp = sorted(components.items(), key=lambda x: abs(x[1]), reverse=True)
    bullish = [{"name": k, "value": round(v, 3)} for k, v in sorted_comp if v > 0.05][:4]
    bearish = [{"name": k, "value": round(v, 3)} for k, v in sorted_comp if v < -0.05][:4]

    # Conflicting signals
    conflicts: list[str] = []
    if score >= 0 and bearish:
        conflicts = [i["name"] for i in bearish[:2]]
    elif score < 0 and bullish:
        conflicts = [i["name"] for i in bullish[:2]]

    # Summary text (concise)
    if abs_score < 0.15:
        summary = f"{asset} 시그널이 뚜렷하지 않아 관망 권장"
    elif score >= 0:
        summary = f"{asset} {strength} 매수 시그널 ({score:+.2f})"
    else:
        summary = f"{asset} {strength} 매도 시그널 ({score:+.2f})"

    return {
        "summary": summary,
        "asset": asset,
        "score": round(score, 4),
        "abs_score": round(abs_score, 4),
        "direction": direction,
        "action": action,
        "strength": strength,
        "strength_level": strength_level,
        "regime": regime,
        "regime_description": regime_desc,
        "bullish_indicators": bullish,
        "bearish_indicators": bearish,
        "conflicts": conflicts,
        "memory_refs": getattr(payload, "memory_count", 0) or 0,
        "formula": getattr(payload, "formula_name", None),
        "strategy": getattr(payload, "strategy_name", None),
    }


def _call_llm_with_api_key(messages: list[dict], max_tokens: int = 512) -> str | None:
    """Try calling LLM using API keys from environment variables."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("LLM_GATEWAY_MODEL")

    if anthropic_key:
        try:
            import httpx
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model or "claude-sonnet-4-6-20250514",
                    "max_tokens": max_tokens,
                    "system": messages[0]["content"] if messages and messages[0]["role"] == "system" else "",
                    "messages": [m for m in messages if m["role"] != "system"],
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", [])
                if content and content[0].get("text"):
                    return content[0]["text"]
        except Exception as exc:
            logger.warning("anthropic_api_call_failed", extra={"error": str(exc)[:200]})

    if openai_key:
        try:
            import httpx
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or "gpt-4o-mini",
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices and choices[0].get("message", {}).get("content"):
                    return choices[0]["message"]["content"]
        except Exception as exc:
            logger.warning("openai_api_call_failed", extra={"error": str(exc)[:200]})

    return None


def _build_combined_reasoning(text: str, payload: ReasoningRequest) -> str:
    """Combine structured JSON + readable text into a single reasoning string."""
    structured = _generate_reasoning_structured(payload)
    return json.dumps({"structured": structured, "text": text}, ensure_ascii=False)


def build_reasoning_text(payload: ReasoningRequest, user_id: str | None = None) -> ReasoningResponse:
    """Generate reasoning using user's OAuth-authenticated LLM, API keys, or structured fallback."""
    structured = _generate_reasoning_structured(payload)

    if not settings.enable_llm:
        # Use structured reasoning when LLM is disabled
        plain_text = generate_structured_reasoning(
            asset=payload.asset,
            signal_score=payload.signal_score,
            strategy_name=payload.strategy_name,
            memory_count=payload.memory_count,
            components=payload.components or {},
            regime=payload.regime,
            formula_name=payload.formula_name,
        )
        combined = json.dumps({"structured": structured, "text": plain_text}, ensure_ascii=False)
        return ReasoningResponse(
            reasoning=combined,
            provider="structured-reasoning",
            structured=structured,
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
                combined = json.dumps({"structured": structured, "text": result}, ensure_ascii=False)
                return ReasoningResponse(reasoning=combined, provider=f"{provider}/oauth", structured=structured)

    # Try API key-based LLM call
    api_result = _call_llm_with_api_key(messages, max_tokens=settings.max_tokens)
    if api_result:
        provider_name = "anthropic/api-key" if os.getenv("ANTHROPIC_API_KEY") else "openai/api-key"
        combined = json.dumps({"structured": structured, "text": api_result}, ensure_ascii=False)
        return ReasoningResponse(reasoning=combined, provider=provider_name, structured=structured)

    # Smart fallback — detailed data-driven reasoning
    fallback_text = _smart_fallback(payload)
    combined = json.dumps({"structured": structured, "text": fallback_text}, ensure_ascii=False)
    return ReasoningResponse(
        reasoning=combined,
        provider="auto-reasoning",
        structured=structured,
    )
