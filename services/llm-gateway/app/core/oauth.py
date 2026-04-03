"""OAuth 2.0 PKCE 인증 — Claude/Codex 구독 연동.

BYOKEY(AprilNEA/BYOKEY) 프로젝트의 Rust 구현을 Python으로 포팅.

유저 플로우:
1. GET /auth/{provider}/login → 브라우저에서 OAuth 로그인 URL 반환
2. 유저가 브라우저에서 Claude/Codex 계정 로그인
3. 콜백으로 authorization code 수신
4. code를 access_token으로 교환
5. 토큰 저장 → 에이전트가 해당 토큰으로 LLM 호출

지원 프로바이더:
- Claude (Anthropic): claude.ai OAuth → console.anthropic.com 토큰 교환
- Codex (OpenAI): auth.openai.com OAuth → 토큰 교환
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger("llm-gateway")

# ── Provider Credentials (from assets.byokey.io) ──────────────────────

PROVIDERS = {
    "claude": {
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "auth_url": "https://claude.ai/oauth/authorize",
        "token_url": "https://console.anthropic.com/v1/oauth/token",
        "redirect_uri": "http://localhost:54545/callback",
        "callback_port": 54545,
        "scopes": ["user:profile", "user:inference"],
    },
    "codex": {
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "auth_url": "https://auth.openai.com/oauth/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "redirect_uri": "http://localhost:1455/auth/callback",
        "callback_port": 1455,
        "scopes": ["openid", "email", "profile", "offline_access"],
    },
}


# ── PKCE ──────────────────────────────────────────────────────────────

def generate_pkce() -> tuple[str, str]:
    """Generate PKCE (code_verifier, code_challenge) pair."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Token Storage (in-memory, per user) ───────────────────────────────

@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0
    provider: str = ""
    user_id: str = ""


# user_id -> {provider -> OAuthToken}
_token_store: dict[str, dict[str, OAuthToken]] = {}
# Pending auth flows: state -> {verifier, provider, user_id}
_pending_flows: dict[str, dict] = {}


def store_token(user_id: str, provider: str, token: OAuthToken) -> None:
    _token_store.setdefault(user_id, {})[provider] = token


def get_token(user_id: str, provider: str) -> OAuthToken | None:
    return _token_store.get(user_id, {}).get(provider)


def has_valid_token(user_id: str, provider: str) -> bool:
    token = get_token(user_id, provider)
    if not token:
        return False
    if token.expires_at and time.time() > token.expires_at - 60:
        return False
    return True


# ── OAuth Flow ────────────────────────────────────────────────────────

def start_auth_flow(provider: str, user_id: str) -> dict:
    """Start OAuth PKCE flow. Returns auth URL for the user to visit."""
    config = PROVIDERS.get(provider)
    if not config:
        return {"error": f"unknown provider: {provider}"}

    verifier, challenge = generate_pkce()
    state = secrets.token_hex(16)

    _pending_flows[state] = {
        "verifier": verifier,
        "provider": provider,
        "user_id": user_id,
    }

    scopes = "+".join(s.replace(":", "%3A") for s in config["scopes"])
    redirect = config["redirect_uri"].replace(":", "%3A").replace("/", "%2F")

    auth_url = (
        f"{config['auth_url']}?"
        f"client_id={config['client_id']}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&redirect_uri={redirect}"
        f"&response_type=code"
        f"&scope={scopes}"
        f"&state={state}"
    )

    return {
        "auth_url": auth_url,
        "state": state,
        "provider": provider,
        "callback_port": config["callback_port"],
    }


async def exchange_code(state: str, code: str) -> OAuthToken | None:
    """Exchange authorization code for access token."""
    flow = _pending_flows.pop(state, None)
    if not flow:
        logger.warning("oauth_unknown_state", extra={"state": state})
        return None

    provider = flow["provider"]
    verifier = flow["verifier"]
    user_id = flow["user_id"]
    config = PROVIDERS[provider]

    # Build token request
    if provider == "claude":
        body = {
            "grant_type": "authorization_code",
            "client_id": config["client_id"],
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "code_verifier": verifier,
            "state": state,
        }
        headers = {"Content-Type": "application/json"}
        try:
            resp = httpx.post(config["token_url"], json=body, headers=headers, timeout=15.0)
        except Exception as exc:
            logger.error("claude_token_exchange_failed", extra={"error": str(exc)})
            return None
    else:
        # Codex uses form-urlencoded
        form_data = {
            "grant_type": "authorization_code",
            "client_id": config["client_id"],
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "code_verifier": verifier,
        }
        headers = {"Accept": "application/json"}
        try:
            resp = httpx.post(config["token_url"], data=form_data, headers=headers, timeout=15.0)
        except Exception as exc:
            logger.error("codex_token_exchange_failed", extra={"error": str(exc)})
            return None

    if resp.status_code != 200:
        logger.error("token_exchange_failed", extra={
            "provider": provider, "status": resp.status_code, "body": resp.text[:200],
        })
        return None

    data = resp.json()
    token = OAuthToken(
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + data.get("expires_in", 3600),
        provider=provider,
        user_id=user_id,
    )

    store_token(user_id, provider, token)
    logger.info("oauth_token_stored", extra={"provider": provider, "user_id": user_id})
    return token


# ── LLM Call with OAuth Token ───────────────────────────���─────────────

def call_with_oauth(user_id: str, provider: str, messages: list[dict], model: str = "", max_tokens: int = 500) -> str | None:
    """Call LLM using user's OAuth token."""
    token = get_token(user_id, provider)
    if not token or not token.access_token:
        return None

    if provider == "claude":
        return _call_claude_api(token.access_token, messages, model or "claude-sonnet-4-5-20250929", max_tokens)
    elif provider == "codex":
        return _call_openai_api(token.access_token, messages, model or "gpt-4o", max_tokens)
    return None


def _call_claude_api(token: str, messages: list[dict], model: str, max_tokens: int) -> str | None:
    """Call Anthropic Messages API with OAuth token."""
    try:
        # Convert OpenAI format to Anthropic format
        system = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": anthropic_messages,
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["content"][0]["text"]
    except Exception as exc:
        logger.warning("claude_api_failed", extra={"error": str(exc)[:200]})
    return None


def _call_openai_api(token: str, messages: list[dict], model: str, max_tokens: int) -> str | None:
    """Call OpenAI Chat Completions API with OAuth token."""
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("openai_api_failed", extra={"error": str(exc)[:200]})
    return None
