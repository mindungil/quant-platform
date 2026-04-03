from fastapi import APIRouter, Header

from app.core.reasoning import build_reasoning_text
from app.core.oauth import (
    start_auth_flow, exchange_code, has_valid_token,
    get_token, call_with_oauth, PROVIDERS,
)
from app.models.reasoning import ReasoningRequest, ReasoningResponse

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "llm-gateway", "providers": list(PROVIDERS.keys())}


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


@router.get("/providers")
def list_providers() -> dict:
    return {"available": list(PROVIDERS.keys())}
