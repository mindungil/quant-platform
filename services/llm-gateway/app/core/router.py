"""LLM Router — OpenClaw/NadirClaw 방식의 지능형 모델 라우팅.

프롬프트 복잡도를 분류하여 적절한 모델로 자동 라우팅:
- simple: 단순 요약, 번역 → 저렴한 모델 (gpt-4o-mini, gemini-flash)
- complex: 시장 분석, 전략 추론 → 프리미엄 모델 (gpt-4o, claude-sonnet)
- reasoning: 수학적 추론, 공식 선택 → 추론 특화 모델 (o3-mini, claude-opus)

Fallback 체인: 실패 시 자동으로 다음 모델로 전환.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("llm-gateway")

# Model tiers — ordered by preference (first = primary, rest = fallback)
MODEL_TIERS = {
    "simple": [
        "gpt-4o-mini",
        "gemini/gemini-2.0-flash",
    ],
    "complex": [
        "gpt-4o",
        "gpt-4o-mini",  # fallback
    ],
    "reasoning": [
        "gpt-4o",
        "gpt-4o-mini",  # fallback
    ],
}


def classify_complexity(prompt: str, components: dict | None = None) -> str:
    """Classify prompt complexity into simple/complex/reasoning.

    Uses heuristics based on:
    - Number of data points (components count)
    - Prompt length
    - Presence of reasoning keywords
    """
    comp_count = len(components) if components else 0
    prompt_len = len(prompt)

    # Reasoning tier: many data points + long prompt + mathematical terms
    reasoning_keywords = ["최적화", "수학", "공식", "계산", "Kelly", "Sharpe", "VaR", "확률"]
    has_reasoning = any(kw in prompt for kw in reasoning_keywords)

    if has_reasoning or comp_count >= 8:
        return "reasoning"

    # Complex tier: multiple indicators, detailed analysis needed
    if comp_count >= 4 or prompt_len > 500:
        return "complex"

    # Simple tier: basic summary
    return "simple"


def get_model_chain(tier: str) -> list[str]:
    """Get the model fallback chain for a tier."""
    return MODEL_TIERS.get(tier, MODEL_TIERS["simple"])


def route_and_call(
    messages: list[dict],
    tier: str | None = None,
    prompt_text: str = "",
    components: dict | None = None,
    max_tokens: int = 500,
    temperature: float = 0.3,
) -> tuple[str, str]:
    """Route to appropriate model and call with fallback.

    Returns (response_text, model_used).
    """
    if tier is None:
        tier = classify_complexity(prompt_text, components)

    model_chain = get_model_chain(tier)

    for model in model_chain:
        try:
            import litellm

            start = time.monotonic()
            response = litellm.completion(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            elapsed = time.monotonic() - start

            content = response.choices[0].message.content or ""

            logger.info("llm_routed", extra={
                "tier": tier,
                "model": model,
                "latency_ms": round(elapsed * 1000),
                "tokens": response.usage.total_tokens if response.usage else 0,
            })

            return content.strip(), model

        except Exception as exc:
            logger.warning("llm_model_failed", extra={
                "model": model,
                "tier": tier,
                "error": str(exc)[:150],
            })
            continue

    # All models failed
    return "", "none"
