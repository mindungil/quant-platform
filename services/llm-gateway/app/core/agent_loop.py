"""Agentic Loop — LLM 멀티턴 도구 호출 루프.

OpenCode/Codex/Claude Desktop 동일 패턴:
  while True:
    response = LLM(messages + tools)
    if response is text → return
    if response is tool_use → execute → append result → continue

Claude API (tool_use) 와 OpenAI API (function_calling) 모두 지원.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from app.core.config import settings
from app.core.oauth import get_token, has_valid_token
from app.core.tools import get_anthropic_tools, get_openai_tools
from app.core.tool_executor import execute_tool

logger = logging.getLogger("llm-gateway")

SYSTEM_PROMPT = """당신은 퀀트 트레이딩 AI 에이전트입니다.
시장 데이터와 기술 지표를 실시간으로 분석하고, 수학적 공식을 활용하여 매매 판단을 수행합니다.

능력:
- 시장 데이터/기술 지표 조회 및 분석
- 시장 레짐(추세/횡보/변동성) 판단
- 수학적 공식 선택 및 평가 (모멘텀, 평균회귀, 돌파 등)
- 리스크 평가 및 포지션 사이징
- 매매 주문 실행 (리스크 승인 후)
- 과거 의사결정 메모리 검색 및 학습
- 공식/전략 등록, 백테스트, 승격
- 자율 모니터링 루프 상태 조회 (paper vs virtual, anomaly, SR 추이)
- 유저 본인 주문/포트폴리오 이력 조회

행동 원칙:
1. 매매 전 반드시 get_trading_rules로 규칙을 확인하세요.
2. 주문 전 반드시 get_risk_assessment로 리스크 승인을 받으세요.
3. 분석은 데이터 기반으로 — 도구를 적극 활용하세요.
4. 판단 과정과 근거를 한국어로 명확히 설명하세요.
5. 불확실할 때는 관망(HOLD)을 추천하세요.
6. 공식 선택 시 현재 시장 레짐을 고려하세요.
7. 과거 유사 상황의 메모리를 참조하세요.

유저 컨텍스트 질의 처리:
- "내 포트폴리오 어때?", "내 PnL 얼마야?" → get_portfolio
- "어제 어떤 주문 났어?", "최근 BTC 매매 이력" → get_recent_orders
- "soak 어떻게 돼가?", "anomaly 뭐 떴어?", "paper vs virtual 차이" → get_loop_state
- 항상 도구 결과를 인용해서 답변하세요. 추측하지 마세요.

한국어로 응답하세요."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AgentResponse:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    provider: str = ""
    loop_count: int = 0
    total_ms: float = 0.0


# ── Claude API (Anthropic) ───────────────────────────────────────────

async def _call_claude(
    token: str,
    messages: list[dict],
    model: str,
    max_tokens: int,
    tools: list[dict],
) -> dict:
    """Call Anthropic Messages API with tools."""
    # Separate system from messages
    system = ""
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            api_messages.append(msg)

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": api_messages,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json=body,
        )
    resp.raise_for_status()
    return resp.json()


def _parse_claude_response(data: dict) -> tuple[str, list[ToolCall], str]:
    """Parse Claude response → (text, tool_calls, stop_reason)."""
    text_parts = []
    tool_calls = []

    for block in data.get("content", []):
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            tool_calls.append(ToolCall(
                id=block["id"],
                name=block["name"],
                arguments=block.get("input", {}),
            ))

    stop_reason = data.get("stop_reason", "end_turn")
    return "\n".join(text_parts), tool_calls, stop_reason


def _build_claude_tool_result(tool_call: ToolCall, result: str) -> dict:
    """Build Anthropic tool_result message."""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": result,
            }
        ],
    }


def _build_claude_assistant_msg(data: dict) -> dict:
    """Preserve full assistant message for conversation continuity."""
    return {"role": "assistant", "content": data.get("content", [])}


# ── OpenAI API ───────────────────────────────────────────────────────

async def _call_openai(
    token: str,
    messages: list[dict],
    model: str,
    max_tokens: int,
    tools: list[dict],
) -> dict:
    """Call OpenAI Chat Completions API with tools."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if tools:
        body["tools"] = tools

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    resp.raise_for_status()
    return resp.json()


def _parse_openai_response(data: dict) -> tuple[str, list[ToolCall], str]:
    """Parse OpenAI response → (text, tool_calls, stop_reason)."""
    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    text = message.get("content", "") or ""
    tool_calls = []

    for tc in message.get("tool_calls", []):
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(ToolCall(
            id=tc["id"],
            name=func.get("name", ""),
            arguments=args,
        ))

    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"
    return text, tool_calls, stop_reason


def _build_openai_tool_result(tool_call: ToolCall, result: str) -> dict:
    """Build OpenAI tool message."""
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": result,
    }


def _build_openai_assistant_msg(data: dict) -> dict:
    """Preserve full assistant message."""
    choice = data.get("choices", [{}])[0]
    return choice.get("message", {"role": "assistant", "content": ""})


# ── OpenAI-Compatible API (OpenRouter, Groq, etc.) ──────────────────

async def _call_openai_compatible(
    token: str,
    messages: list[dict],
    model: str,
    max_tokens: int,
    tools: list[dict],
    base_url: str,
) -> dict:
    """Call any OpenAI-compatible API endpoint."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if tools:
        body["tools"] = tools

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    resp.raise_for_status()
    return resp.json()


# ── Main Agent Loop ──────────────────────────────────────────────────

async def run_agent_loop(
    user_message: str,
    user_id: str,
    conversation_history: list[dict] | None = None,
) -> AgentResponse:
    """Run the agentic loop — LLM calls tools until it produces a final text response.

    This is the core pattern shared by OpenCode, Codex CLI, and Claude Desktop.
    """
    start_time = time.monotonic()

    # 1. Check if user has OAuth token (OPTIONAL premium feature)
    provider = None
    token_str = ""
    for p in ("claude", "codex"):
        if has_valid_token(user_id, p):
            tok = get_token(user_id, p)
            if tok and tok.access_token:
                provider = p
                token_str = tok.access_token
                break

    # 2. If no user OAuth → use platform keys (fallback chain)
    if not provider:
        pollinations_key = os.environ.get("POLLINATIONS") or ""
        groq_key = os.environ.get("groq") or os.environ.get("GROQ_API_KEY") or ""
        github_key = os.environ.get("GITHUB_MODEL_TOKEN") or ""
        if pollinations_key:
            provider = "pollinations"
            token_str = pollinations_key
        elif groq_key:
            provider = "groq"
            token_str = groq_key
        elif github_key:
            provider = "github"
            token_str = github_key
        else:
            return AgentResponse(
                text="플랫폼 LLM이 설정되지 않았습니다. 관리자에게 문의하세요.",
                provider="none",
                total_ms=(time.monotonic() - start_time) * 1000,
            )

    # Build messages
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    # Select API functions based on provider
    if provider == "claude":
        model = "claude-sonnet-4-5-20250929"
        tools = get_anthropic_tools()
        call_fn = _call_claude
        parse_fn = _parse_claude_response
        build_result_fn = _build_claude_tool_result
        build_assistant_fn = _build_claude_assistant_msg
    elif provider == "github":
        model = "gpt-4o"
        tools = get_openai_tools()
        call_fn = lambda tok, msgs, mdl, mt, tls: _call_openai_compatible(
            tok, msgs, mdl, mt, tls, "https://models.inference.ai.azure.com/chat/completions"
        )
        parse_fn = _parse_openai_response
        build_result_fn = _build_openai_tool_result
        build_assistant_fn = _build_openai_assistant_msg
    elif provider == "openrouter":
        model = "anthropic/claude-sonnet-4"
        tools = get_openai_tools()  # OpenRouter uses OpenAI-compatible format
        call_fn = lambda tok, msgs, mdl, mt, tls: _call_openai_compatible(
            tok, msgs, mdl, mt, tls, "https://openrouter.ai/api/v1/chat/completions"
        )
        parse_fn = _parse_openai_response
        build_result_fn = _build_openai_tool_result
        build_assistant_fn = _build_openai_assistant_msg
    elif provider == "pollinations":
        model = "nova-fast"
        tools = get_openai_tools()
        call_fn = lambda tok, msgs, mdl, mt, tls: _call_openai_compatible(
            tok, msgs, mdl, mt, tls, "https://gen.pollinations.ai/v1/chat/completions"
        )
        parse_fn = _parse_openai_response
        build_result_fn = _build_openai_tool_result
        build_assistant_fn = _build_openai_assistant_msg
    elif provider == "groq":
        model = "llama-3.3-70b-versatile"
        tools = get_openai_tools()
        call_fn = lambda tok, msgs, mdl, mt, tls: _call_openai_compatible(
            tok, msgs, mdl, mt, tls, "https://api.groq.com/openai/v1/chat/completions"
        )
        parse_fn = _parse_openai_response
        build_result_fn = _build_openai_tool_result
        build_assistant_fn = _build_openai_assistant_msg
    else:
        model = "gpt-4o"
        tools = get_openai_tools()
        call_fn = _call_openai
        parse_fn = _parse_openai_response
        build_result_fn = _build_openai_tool_result
        build_assistant_fn = _build_openai_assistant_msg

    max_loops = settings.agent_max_loops
    all_tool_calls: list[dict] = []
    loop_count = 0

    # ── The Loop ─────────────────────────────────────────────────
    while loop_count < max_loops:
        loop_count += 1

        try:
            raw_response = await call_fn(
                token_str, messages, model, settings.agent_max_tokens, tools,
            )
        except httpx.HTTPStatusError as exc:
            logger.error("llm_api_error", extra={
                "provider": provider, "status": exc.response.status_code,
                "body": exc.response.text[:200],
            })
            return AgentResponse(
                text=f"LLM API 오류 ({exc.response.status_code}). 잠시 후 다시 시도해주세요.",
                provider=f"{provider}/oauth",
                loop_count=loop_count,
                total_ms=(time.monotonic() - start_time) * 1000,
            )
        except Exception as exc:
            logger.error("llm_call_failed", extra={"provider": provider, "error": str(exc)[:200]})
            return AgentResponse(
                text=f"LLM 호출 실패: {str(exc)[:200]}",
                provider=f"{provider}/oauth",
                loop_count=loop_count,
                total_ms=(time.monotonic() - start_time) * 1000,
            )

        text, tool_calls, stop_reason = parse_fn(raw_response)

        # Append assistant message to history
        assistant_msg = build_assistant_fn(raw_response)
        messages.append(assistant_msg)

        # If no tool calls → done
        if not tool_calls:
            return AgentResponse(
                text=text,
                tool_calls=all_tool_calls,
                provider=f"{provider}/oauth",
                loop_count=loop_count,
                total_ms=(time.monotonic() - start_time) * 1000,
            )

        # Execute each tool call
        for tc in tool_calls:
            tool_record = {
                "tool_name": tc.name,
                "arguments": tc.arguments,
                "result": None,
                "error": None,
            }

            result_str = await execute_tool(tc.name, tc.arguments, user_id)
            tool_record["result"] = result_str[:500]  # Truncate for response
            all_tool_calls.append(tool_record)

            # Append tool result to messages
            result_msg = build_result_fn(tc, result_str)
            messages.append(result_msg)

        logger.info("agent_loop_iteration", extra={
            "loop": loop_count,
            "tools_called": [tc.name for tc in tool_calls],
            "user_id": user_id,
        })

    # Max loops reached
    final_text = text if text else "분석 루프가 최대 횟수에 도달했습니다. 현재까지의 결과를 참조해주세요."
    return AgentResponse(
        text=final_text,
        tool_calls=all_tool_calls,
        provider=f"{provider}/oauth",
        loop_count=loop_count,
        total_ms=(time.monotonic() - start_time) * 1000,
    )
