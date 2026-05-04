"""Agent Chat System — Integration Tests.

10개 시나리오:
1. 도구 스키마 정합성
2. 도구 실행기 디스패치
3. 에이전틱 루프 (모킹)
4. 대화 히스토리 CRUD
5. Chat API 엔드포인트
6. 단순 질문 (도구 없이 응답)
7. 시장 분석 요청 (다중 도구 호출)
8. 매매 실행 요청 (리스크 → 주문)
9. OAuth 토큰 없을 때 폴백
10. 루프 제한 초과 방어
"""
import json
import sys
import os
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# Add project paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "llm-gateway"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

passed = 0
failed = 0
total = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} — {detail}")


# ── Scenario 1: Tool Schema Validation ───────────────────────────────
print("\n=== Scenario 1: 도구 스키마 정합성 ===")

from app.core.tools import TOOL_DEFINITIONS, TOOL_MAP, get_anthropic_tools, get_openai_tools

check("총 도구 수 >= 18", len(TOOL_DEFINITIONS) >= 18, f"actual: {len(TOOL_DEFINITIONS)}")
check("TOOL_MAP 동기화", len(TOOL_MAP) == len(TOOL_DEFINITIONS))

for t in TOOL_DEFINITIONS:
    check(f"도구 '{t['name']}' — name 존재", bool(t.get("name")))
    check(f"도구 '{t['name']}' — description 존재", bool(t.get("description")))
    check(f"도구 '{t['name']}' — input_schema 존재", "input_schema" in t)
    schema = t["input_schema"]
    check(f"도구 '{t['name']}' — type=object", schema.get("type") == "object")
    check(f"도구 '{t['name']}' — properties 존재", "properties" in schema)

# Anthropic format
anthropic_tools = get_anthropic_tools()
check("Anthropic 포맷 변환", len(anthropic_tools) == len(TOOL_DEFINITIONS))
for at in anthropic_tools:
    check(f"Anthropic '{at['name']}' keys", set(at.keys()) == {"name", "description", "input_schema"})

# OpenAI format
openai_tools = get_openai_tools()
check("OpenAI 포맷 변환", len(openai_tools) == len(TOOL_DEFINITIONS))
for ot in openai_tools:
    check(f"OpenAI '{ot['function']['name']}' type", ot["type"] == "function")
    check(f"OpenAI '{ot['function']['name']}' keys", "parameters" in ot["function"])


# ── Scenario 2: Tool Executor Dispatch ───────────────────────────────
print("\n=== Scenario 2: 도구 실행기 디스패치 ===")

from app.core.tool_executor import _HANDLERS, execute_tool

expected_tools = [
    "get_market_data", "get_features", "get_signal", "get_portfolio",
    "detect_regime", "search_memory", "search_formula_outcomes",
    "get_risk_assessment", "evaluate_formula", "place_order",
    "store_memory", "list_formulas", "list_strategies",
    "register_formula", "promote_formula",
    "get_trading_rules", "get_formula_guide", "full_market_analysis",
]

for tool_name in expected_tools:
    check(f"핸들러 등록: {tool_name}", tool_name in _HANDLERS)

# Unknown tool returns error
result = asyncio.get_event_loop().run_until_complete(
    execute_tool("nonexistent_tool", {}, "test-user")
)
parsed = json.loads(result)
check("미등록 도구 에러 반환", "error" in parsed, f"result: {parsed}")

# list_formulas works locally (no HTTP needed)
result = asyncio.get_event_loop().run_until_complete(
    execute_tool("list_formulas", {}, "test-user")
)
parsed = json.loads(result)
check("list_formulas 로컬 실행", "formulas" in parsed, f"result keys: {list(parsed.keys())}")
check("list_formulas 결과 > 0", len(parsed.get("formulas", [])) > 0, f"count: {len(parsed.get('formulas', []))}")

# get_formula_guide works locally
result = asyncio.get_event_loop().run_until_complete(
    execute_tool("get_formula_guide", {}, "test-user")
)
parsed = json.loads(result)
check("get_formula_guide 로컬 실행", "guide" in parsed)
check("guide에 trending 존재", "trending" in parsed.get("guide", {}))

# get_trading_rules (with mock)
with patch("app.core.tool_executor._client") as mock_client:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"max_single_asset_weight": 0.3, "warning_drawdown": 0.05}
    mock_resp.raise_for_status = MagicMock()

    async def mock_get(*a, **kw):
        return mock_resp
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = asyncio.get_event_loop().run_until_complete(
        execute_tool("get_trading_rules", {}, "test-user")
    )
    parsed = json.loads(result)
    check("get_trading_rules 실행", "rules" in parsed)
    check("규칙 7개 이상", len(parsed.get("rules", [])) >= 7, f"count: {len(parsed.get('rules', []))}")


# ── Scenario 3: Agent Loop Structure ─────────────────────────────────
print("\n=== Scenario 3: 에이전틱 루프 구조 ===")

from app.core.agent_loop import (
    SYSTEM_PROMPT,
    ToolCall,
    AgentResponse,
    _parse_claude_response,
    _parse_openai_response,
    _build_claude_tool_result,
    _build_openai_tool_result,
)

check("시스템 프롬프트 한국어", "퀀트" in SYSTEM_PROMPT)
check("도구 사용 규칙 포함", "get_trading_rules" in SYSTEM_PROMPT)
check("리스크 규칙 포함", "get_risk_assessment" in SYSTEM_PROMPT)

# Parse Claude response — text only
claude_text_resp = {
    "content": [{"type": "text", "text": "분석 결과입니다."}],
    "stop_reason": "end_turn",
}
text, tools, stop = _parse_claude_response(claude_text_resp)
check("Claude 텍스트 파싱", text == "분석 결과입니다.")
check("Claude 도구 없음", len(tools) == 0)
check("Claude end_turn", stop == "end_turn")

# Parse Claude response — tool use
claude_tool_resp = {
    "content": [
        {"type": "text", "text": "시장 데이터를 조회하겠습니다."},
        {"type": "tool_use", "id": "tc_123", "name": "get_market_data", "input": {"asset": "BTCUSDT"}},
    ],
    "stop_reason": "tool_use",
}
text, tools, stop = _parse_claude_response(claude_tool_resp)
check("Claude tool_use 파싱", len(tools) == 1)
check("Claude tool name", tools[0].name == "get_market_data")
check("Claude tool args", tools[0].arguments == {"asset": "BTCUSDT"})
check("Claude tool id", tools[0].id == "tc_123")

# Build Claude tool result
result_msg = _build_claude_tool_result(tools[0], '{"close": 65000}')
check("Claude tool_result format", result_msg["role"] == "user")
check("Claude tool_result content", result_msg["content"][0]["type"] == "tool_result")
check("Claude tool_result id", result_msg["content"][0]["tool_use_id"] == "tc_123")

# Parse OpenAI response
openai_resp = {
    "choices": [{
        "message": {
            "content": "분석합니다.",
            "tool_calls": [{
                "id": "call_456",
                "function": {"name": "get_features", "arguments": '{"asset": "ETHUSDT"}'},
            }],
        },
        "finish_reason": "tool_calls",
    }],
}
text, tools, stop = _parse_openai_response(openai_resp)
check("OpenAI tool_calls 파싱", len(tools) == 1)
check("OpenAI tool name", tools[0].name == "get_features")
check("OpenAI tool args", tools[0].arguments == {"asset": "ETHUSDT"})
check("OpenAI stop_reason", stop == "tool_use")

# Build OpenAI tool result
result_msg = _build_openai_tool_result(tools[0], '{"rsi_14": 45.2}')
check("OpenAI tool result format", result_msg["role"] == "tool")
check("OpenAI tool_call_id", result_msg["tool_call_id"] == "call_456")

# AgentResponse
resp = AgentResponse(
    text="테스트",
    tool_calls=[{"tool_name": "get_features", "arguments": {"asset": "BTC"}}],
    provider="claude/oauth",
    loop_count=2,
    total_ms=1500.0,
)
check("AgentResponse 생성", resp.text == "테스트")
check("AgentResponse tool_calls", len(resp.tool_calls) == 1)


# ── Scenario 4: Conversation History ─────────────────────────────────
print("\n=== Scenario 4: 대화 히스토리 (모킹) ===")

# Mock SqlStore to avoid DB dependency
with patch("app.db.conversation._get_store") as mock_store_fn:
    store = MagicMock()
    mock_store_fn.return_value = store
    store.execute = MagicMock()
    store.fetch_all = MagicMock(return_value=[])
    store.fetch_one = MagicMock(return_value=None)

    from app.db.conversation import (
        create_conversation,
        list_conversations,
        save_message,
        get_messages,
        get_llm_context,
        get_conversation,
    )

    conv = create_conversation("user1", "테스트 대화")
    check("대화 생성 — conversation_id", "conversation_id" in conv)
    check("대화 생성 — user_id", conv["user_id"] == "user1")
    check("대화 생성 — title", conv["title"] == "테스트 대화")
    check("대화 생성 — DB execute 호출", store.execute.called)

    msg = save_message(conv["conversation_id"], "user", "안녕하세요")
    check("메시지 저장 — message_id", "message_id" in msg)
    check("메시지 저장 — role", msg["role"] == "user")

    msg2 = save_message(
        conv["conversation_id"], "assistant", "분석 결과입니다.",
        tool_calls=[{"tool_name": "get_market_data", "arguments": {"asset": "BTC"}}],
    )
    check("도구 호출 메시지 저장", msg2["role"] == "assistant")

    # Mock return for get_messages — implementation uses dict-row access
    store.fetch_all.return_value = [
        {"message_id": "m1", "role": "user", "content": "안녕",
         "tool_calls": None, "tool_name": None, "tool_call_id": None, "created_at": None},
        {"message_id": "m2", "role": "assistant", "content": "응답",
         "tool_calls": None, "tool_name": None, "tool_call_id": None, "created_at": None},
    ]
    msgs = get_messages(conv["conversation_id"])
    check("메시지 조회 반환", len(msgs) == 2)
    check("메시지 순서 (oldest first)", msgs[0]["role"] == "user")

    # LLM context
    store.fetch_all.return_value = [
        {"role": "user", "content": "질문"},
        {"role": "assistant", "content": "답변"},
    ]
    ctx = get_llm_context(conv["conversation_id"])
    check("LLM 컨텍스트 반환", len(ctx) == 2)
    check("LLM 컨텍스트 형식", set(ctx[0].keys()) == {"role", "content"})


# ── Scenario 5: Chat API Model Validation ────────────────────────────
print("\n=== Scenario 5: Chat API 모델 검증 ===")

from app.models.chat import ChatRequest, ChatResponse, ToolCallRecord, ConversationSummary
from pydantic import ValidationError

# Valid request
req = ChatRequest(message="BTC 분석해줘")
check("ChatRequest 생성", req.message == "BTC 분석해줘")
check("ChatRequest conversation_id 없음", req.conversation_id is None)

# With conversation_id
req2 = ChatRequest(message="후속 질문", conversation_id="conv-123")
check("ChatRequest with conv_id", req2.conversation_id == "conv-123")

# Empty message validation
try:
    ChatRequest(message="")
    check("빈 메시지 거부", False, "should have raised")
except ValidationError:
    check("빈 메시지 거부", True)

# ChatResponse
resp = ChatResponse(
    conversation_id="conv-1",
    message_id="msg-1",
    text="분석 완료",
    tool_calls=[ToolCallRecord(tool_name="get_features", arguments={"asset": "BTC"}, result='{"rsi": 45}')],
    provider="claude/oauth",
    loop_count=2,
    elapsed_ms=1500.0,
)
check("ChatResponse 생성", resp.text == "분석 완료")
check("ChatResponse tool_calls", len(resp.tool_calls) == 1)
check("ChatResponse provider", resp.provider == "claude/oauth")

# ConversationSummary
conv = ConversationSummary(
    conversation_id="c-1",
    user_id="u-1",
    title="BTC 분석",
)
check("ConversationSummary 생성", conv.title == "BTC 분석")


# ── Scenario 6: Agent Loop — No Token Fallback ───────────────────────
print("\n=== Scenario 6: OAuth 토큰 없을 때 폴백 ===")

from app.core.agent_loop import run_agent_loop

with patch("app.core.agent_loop.has_valid_token", return_value=False):
    result = asyncio.get_event_loop().run_until_complete(
        run_agent_loop("BTC 분석해줘", "user-no-token")
    )
    check("토큰 없음 — provider=none", result.provider == "none")
    check(
        "토큰 없음 — 안내 메시지",
        any(kw in result.text for kw in ["연결", "연동", "설정", "관리자", "LLM"]),
    )
    check("토큰 없음 — 도구 호출 없음", len(result.tool_calls) == 0)


# ── Scenario 7: Agent Loop — Text Only Response ──────────────────────
print("\n=== Scenario 7: 단순 질문 (도구 없이 응답) ===")

with patch("app.core.agent_loop.has_valid_token", return_value=True), \
     patch("app.core.agent_loop.get_token") as mock_get_token, \
     patch("app.core.agent_loop._call_claude") as mock_call:

    mock_token = MagicMock()
    mock_token.access_token = "test-token"
    mock_get_token.return_value = mock_token

    mock_call.return_value = {
        "content": [{"type": "text", "text": "안녕하세요! 무엇을 도와드릴까요?"}],
        "stop_reason": "end_turn",
    }

    result = asyncio.get_event_loop().run_until_complete(
        run_agent_loop("안녕", "user-1")
    )
    check("단순 질문 — 텍스트 응답", "안녕하세요" in result.text)
    check("단순 질문 — 도구 호출 없음", len(result.tool_calls) == 0)
    check("단순 질문 — 루프 1회", result.loop_count == 1)
    check("단순 질문 — provider", "claude" in result.provider)


# ── Scenario 8: Agent Loop — Multi-tool Market Analysis ──────────────
print("\n=== Scenario 8: 시장 분석 (다중 도구 호출) ===")

call_count = 0

async def mock_claude_multi(*args, **kwargs):
    global call_count
    call_count += 1
    if call_count == 1:
        return {
            "content": [
                {"type": "text", "text": "시장 데이터를 조회하겠습니다."},
                {"type": "tool_use", "id": "tc_1", "name": "get_features", "input": {"asset": "BTCUSDT"}},
                {"type": "tool_use", "id": "tc_2", "name": "detect_regime", "input": {"asset": "BTCUSDT"}},
            ],
            "stop_reason": "tool_use",
        }
    elif call_count == 2:
        return {
            "content": [
                {"type": "text", "text": "메모리를 검색합니다."},
                {"type": "tool_use", "id": "tc_3", "name": "search_memory", "input": {"asset": "BTCUSDT"}},
            ],
            "stop_reason": "tool_use",
        }
    else:
        return {
            "content": [{"type": "text", "text": "BTC는 현재 상승 추세입니다. RSI 65, MACD 양전환."}],
            "stop_reason": "end_turn",
        }

with patch("app.core.agent_loop.has_valid_token", return_value=True), \
     patch("app.core.agent_loop.get_token") as mock_get_token, \
     patch("app.core.agent_loop._call_claude", side_effect=mock_claude_multi), \
     patch("app.core.tool_executor.execute_tool") as mock_exec:

    mock_token = MagicMock()
    mock_token.access_token = "test-token"
    mock_get_token.return_value = mock_token
    mock_exec.return_value = '{"status": "ok"}'
    call_count = 0

    result = asyncio.get_event_loop().run_until_complete(
        run_agent_loop("BTC 현재 상황 분석해줘", "user-1")
    )
    check("멀티도구 — 최종 텍스트", "상승" in result.text)
    check("멀티도구 — 3개 도구 호출", len(result.tool_calls) == 3, f"actual: {len(result.tool_calls)}")
    check("멀티도구 — 루프 3회", result.loop_count == 3)
    tool_names = [tc["tool_name"] for tc in result.tool_calls]
    check("멀티도구 — get_features 호출", "get_features" in tool_names)
    check("멀티도구 — detect_regime 호출", "detect_regime" in tool_names)
    check("멀티도구 — search_memory 호출", "search_memory" in tool_names)


# ── Scenario 9: Agent Loop — Order Flow ──────────────────────────────
print("\n=== Scenario 9: 매매 실행 (리스크→주문) ===")

order_call_count = 0

async def mock_claude_order(*args, **kwargs):
    global order_call_count
    order_call_count += 1
    if order_call_count == 1:
        return {
            "content": [
                {"type": "text", "text": "먼저 매매 규칙과 리스크를 확인하겠습니다."},
                {"type": "tool_use", "id": "tc_r1", "name": "get_trading_rules", "input": {}},
                {"type": "tool_use", "id": "tc_r2", "name": "get_risk_assessment", "input": {"asset": "BTCUSDT", "requested_notional": 100}},
            ],
            "stop_reason": "tool_use",
        }
    elif order_call_count == 2:
        return {
            "content": [
                {"type": "text", "text": "리스크 승인 완료. 주문을 실행합니다."},
                {"type": "tool_use", "id": "tc_o1", "name": "place_order", "input": {
                    "asset": "BTCUSDT", "side": "BUY", "quantity": 0.001,
                    "requested_notional": 100, "stop_loss_pct": 0.03, "take_profit_pct": 0.05,
                }},
            ],
            "stop_reason": "tool_use",
        }
    else:
        return {
            "content": [{"type": "text", "text": "BTC 0.001개 매수 주문을 실행했습니다. 손절 3%, 익절 5% 설정."}],
            "stop_reason": "end_turn",
        }

with patch("app.core.agent_loop.has_valid_token", return_value=True), \
     patch("app.core.agent_loop.get_token") as mock_get_token, \
     patch("app.core.agent_loop._call_claude", side_effect=mock_claude_order), \
     patch("app.core.tool_executor.execute_tool") as mock_exec:

    mock_token = MagicMock()
    mock_token.access_token = "test-token"
    mock_get_token.return_value = mock_token
    mock_exec.return_value = '{"approved": true, "order_id": "ord-123"}'
    order_call_count = 0

    result = asyncio.get_event_loop().run_until_complete(
        run_agent_loop("BTC $100어치 매수해줘", "user-1")
    )
    check("주문 — 최종 응답", "매수" in result.text)
    check("주문 — 도구 3개 호출", len(result.tool_calls) == 3, f"actual: {len(result.tool_calls)}")
    tool_names = [tc["tool_name"] for tc in result.tool_calls]
    check("주문 — 규칙 먼저 확인", "get_trading_rules" in tool_names)
    check("주문 — 리스크 확인", "get_risk_assessment" in tool_names)
    check("주문 — 주문 실행", "place_order" in tool_names)
    check("주문 — 루프 3회", result.loop_count == 3)


# ── Scenario 10: Loop Limit Defense ──────────────────────────────────
print("\n=== Scenario 10: 루프 제한 초과 방어 ===")

async def mock_claude_infinite(*args, **kwargs):
    return {
        "content": [
            {"type": "tool_use", "id": f"tc_inf", "name": "get_features", "input": {"asset": "BTC"}},
        ],
        "stop_reason": "tool_use",
    }

with patch("app.core.agent_loop.has_valid_token", return_value=True), \
     patch("app.core.agent_loop.get_token") as mock_get_token, \
     patch("app.core.agent_loop._call_claude", side_effect=mock_claude_infinite), \
     patch("app.core.tool_executor.execute_tool") as mock_exec, \
     patch("app.core.agent_loop.settings") as mock_settings:

    mock_token = MagicMock()
    mock_token.access_token = "test-token"
    mock_get_token.return_value = mock_token
    mock_exec.return_value = '{"data": "ok"}'
    mock_settings.agent_max_loops = 5
    mock_settings.agent_max_tokens = 2000

    result = asyncio.get_event_loop().run_until_complete(
        run_agent_loop("무한 분석", "user-1")
    )
    check("루프 제한 — 5회 중단", result.loop_count == 5)
    check("루프 제한 — 도구 5개", len(result.tool_calls) == 5)
    check("루프 제한 — 텍스트 있음", len(result.text) > 0)


# ── Summary ──────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"결과: {passed}/{total} 통과, {failed} 실패")
print(f"{'='*60}")
if failed:
    sys.exit(1)
