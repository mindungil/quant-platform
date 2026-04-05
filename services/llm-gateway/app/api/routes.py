import uuid

from fastapi import APIRouter, Header

from app.core.reasoning import build_reasoning_text
from app.core.oauth import (
    start_auth_flow, exchange_code, has_valid_token,
    get_token, call_with_oauth, PROVIDERS,
    start_device_flow, get_device_flow_status,
)
from app.core.agent_loop import run_agent_loop
from app.db.conversation import (
    create_conversation, list_conversations, get_conversation,
    save_message, get_messages, get_llm_context, update_conversation_title,
)
from app.models.reasoning import ReasoningRequest, ReasoningResponse
from app.models.chat import (
    ChatRequest, ChatResponse, ToolCallRecord,
    ConversationSummary, MessageRecord,
)

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "llm-gateway", "providers": list(PROVIDERS.keys())}


# ── Chat (에이전트 대화) ──────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    x_user_id: str | None = Header(default=None),
) -> ChatResponse:
    """에이전트와 대화 — 에이전틱 루프로 도구 호출 + 응답 생성."""
    user_id = x_user_id or "anonymous"

    # Get or create conversation
    conv_id = req.conversation_id
    if not conv_id:
        conv = create_conversation(user_id, title=req.message[:50])
        conv_id = conv["conversation_id"]

    # Save user message
    user_msg = save_message(conv_id, "user", req.message)

    # Load conversation history for context
    history = get_llm_context(conv_id, max_messages=30)
    # Remove the message we just added (it will be in user_message param)
    if history and history[-1]["content"] == req.message:
        history = history[:-1]

    # Run agentic loop
    result = await run_agent_loop(
        user_message=req.message,
        user_id=user_id,
        conversation_history=history if history else None,
    )

    # Save assistant response
    assistant_msg = save_message(
        conv_id, "assistant", result.text,
        tool_calls=result.tool_calls if result.tool_calls else None,
    )

    # Auto-generate title from first message
    conv = get_conversation(conv_id)
    if conv and not conv.get("title"):
        update_conversation_title(conv_id, req.message[:50])

    return ChatResponse(
        conversation_id=conv_id,
        message_id=assistant_msg["message_id"],
        text=result.text,
        tool_calls=[ToolCallRecord(**tc) for tc in result.tool_calls],
        provider=result.provider,
        loop_count=result.loop_count,
        elapsed_ms=result.total_ms,
    )


@router.get("/conversations")
def get_conversations(
    x_user_id: str | None = Header(default=None),
    limit: int = 20,
) -> list[ConversationSummary]:
    """유저의 대화 목록 조회."""
    user_id = x_user_id or "anonymous"
    convs = list_conversations(user_id, limit=limit)
    return [ConversationSummary(**c) for c in convs]


@router.get("/conversations/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: str,
    x_user_id: str | None = Header(default=None),
    limit: int = 50,
) -> list[MessageRecord]:
    """대화의 메시지 목록 조회."""
    msgs = get_messages(conversation_id, limit=limit)
    return [MessageRecord(**m) for m in msgs]


# ── Reasoning (기존 단일 호출) ─────────────────────────────────────────

@router.post("/reasoning/generate", response_model=ReasoningResponse)
def generate_reasoning(payload: ReasoningRequest, x_user_id: str | None = Header(default=None)) -> ReasoningResponse:
    return build_reasoning_text(payload, user_id=x_user_id)


# ── OAuth 인증 ─────────────────────────────────────────────────────────

@router.get("/auth/{provider}/login")
def oauth_login(provider: str, x_user_id: str | None = Header(default=None)) -> dict:
    """OAuth 로그인 시작 — 브라우저에서 열 URL 반환."""
    user_id = x_user_id or "anonymous"
    return start_auth_flow(provider, user_id)


@router.get("/auth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str) -> dict:
    """OAuth 콜백 — authorization code를 token으로 교환."""
    token = await exchange_code(state, code)
    if token:
        return {"status": "authenticated", "provider": provider, "user_id": token.user_id}
    return {"status": "failed", "provider": provider}


@router.get("/auth/{provider}/status")
def oauth_status(provider: str, x_user_id: str | None = Header(default=None)) -> dict:
    """유저의 프로바이더 인증 상태 확인."""
    user_id = x_user_id or "anonymous"
    valid = has_valid_token(user_id, provider)
    return {"provider": provider, "user_id": user_id, "authenticated": valid}


@router.post("/auth/github-copilot/device-start")
async def copilot_device_start(x_user_id: str | None = Header(default=None)) -> dict:
    """GitHub Copilot Device Flow 시작 — user_code와 verification_uri 반환."""
    user_id = x_user_id or "anonymous"
    result = await start_device_flow(user_id)
    return result


@router.post("/auth/github-copilot/device-poll")
async def copilot_device_poll(x_user_id: str | None = Header(default=None)) -> dict:
    """Device Flow polling 상태 확인."""
    user_id = x_user_id or "anonymous"
    return await get_device_flow_status(user_id)


@router.get("/providers")
def list_providers() -> dict:
    return {"available": list(PROVIDERS.keys())}


# ── Tools (도구 목록 조회) ─────────────────────────────────────────────

@router.get("/tools")
def get_tools() -> dict:
    """에이전트가 사용 가능한 도구 목록."""
    from app.core.tools import TOOL_DEFINITIONS
    return {
        "total": len(TOOL_DEFINITIONS),
        "tools": [
            {"name": t["name"], "description": t["description"]}
            for t in TOOL_DEFINITIONS
        ],
    }
