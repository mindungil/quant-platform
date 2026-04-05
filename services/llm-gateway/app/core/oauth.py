"""OAuth 2.0 PKCE 인증 + GitHub Copilot Device Flow — Claude/Codex/Copilot 구독 연동.

BYOKEY(AprilNEA/BYOKEY) 프로젝트의 Rust 구현을 Python으로 포팅.

유저 플로우 (PKCE):
1. GET /auth/{provider}/login → 브라우저에서 OAuth 로그인 URL 반환
2. 유저가 브라우저에서 Claude/Codex 계정 로그인
3. 콜백으로 authorization code 수신
4. code를 access_token으로 교환
5. 토큰 저장 → 에이전트가 해당 토큰으로 LLM 호출

유저 플로우 (Device Flow — GitHub Copilot):
1. POST /auth/github-copilot/device-start → user_code, verification_uri 반환
2. 유저가 브라우저에서 github.com/login/device 접속 → user_code 입력
3. 서버가 백그라운드로 polling → access_token 수신
4. GitHub access_token → Copilot 토큰 교환
5. 30분마다 Copilot 토큰 자동 갱신

지원 프로바이더:
- Claude (Anthropic): claude.ai OAuth → console.anthropic.com 토큰 교환
- Codex (OpenAI): auth.openai.com OAuth → 토큰 교환
- GitHub Copilot: Device Flow → GitHub token → Copilot token 교환
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import httpx
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("llm-gateway")

# ── Provider Credentials (from assets.byokey.io) ────────────────────────

_redirect_base = f"http://{settings.public_host}:{settings.llm_gateway_port}"

PROVIDERS = {
    "claude": {
        "type": "pkce",
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "auth_url": "https://claude.ai/oauth/authorize",
        "token_url": "https://console.anthropic.com/v1/oauth/token",
        "redirect_uri": f"{_redirect_base}/auth/claude/callback",
        "callback_port": settings.llm_gateway_port,
        "scopes": ["user:profile", "user:inference"],
    },
    "codex": {
        "type": "pkce",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "auth_url": "https://auth.openai.com/oauth/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "redirect_uri": f"{_redirect_base}/auth/codex/callback",
        "callback_port": settings.llm_gateway_port,
        "scopes": ["openid", "email", "profile", "offline_access"],
    },
    "github-copilot": {
        "type": "device_flow",
        "client_id": "Iv1.b507a08c87ecfe98",
        "device_url": "https://github.com/login/device/code",
        "token_url": "https://github.com/login/oauth/access_token",
        "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
        "chat_url": "https://api.githubcopilot.com/chat/completions",
        "scope": "user copilot",
    },
}


# ── PKCE ─────────────────────────────────────────────────────────────────

def generate_pkce() -> tuple[str, str]:
    """Generate PKCE (code_verifier, code_challenge) pair."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Token Storage ────────────────────────────────────────────────────────

@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0
    provider: str = ""
    user_id: str = ""
    github_access_token: str | None = None


# In-memory fallback: user_id -> {provider -> OAuthToken}
_token_store: dict[str, dict[str, OAuthToken]] = {}
# Pending auth flows: state -> {verifier, provider, user_id}
_pending_flows: dict[str, dict] = {}
# Device flow status: user_id -> status dict
_device_flow_status: dict[str, dict] = {}


def store_token(user_id: str, provider: str, token: OAuthToken) -> None:
    _token_store.setdefault(user_id, {})[provider] = token


def get_token(user_id: str, provider: str) -> OAuthToken | None:
    return _token_store.get(user_id, {}).get(provider)


# ── Redis Token Storage (async) ──────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
_redis: aioredis.Redis | None = None

TOKEN_KEY = "llm_gateway:token:{user_id}:{provider}"
TOKEN_TTL = 86400  # 24시간


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def store_token_async(user_id: str, provider: str, token: OAuthToken) -> None:
    """Store token in Redis and in-memory fallback."""
    store_token(user_id, provider, token)
    try:
        r = _get_redis()
        key = TOKEN_KEY.format(user_id=user_id, provider=provider)
        await r.setex(key, TOKEN_TTL, json.dumps(dataclasses.asdict(token)))
    except Exception as exc:
        logger.warning("redis_store_failed", extra={"error": str(exc)[:200]})


async def get_token_async(user_id: str, provider: str) -> OAuthToken | None:
    """Get token from Redis, falling back to in-memory."""
    try:
        r = _get_redis()
        key = TOKEN_KEY.format(user_id=user_id, provider=provider)
        data = await r.get(key)
        if data:
            return OAuthToken(**json.loads(data))
    except Exception as exc:
        logger.warning("redis_get_failed", extra={"error": str(exc)[:200]})
    return get_token(user_id, provider)


def has_valid_token(user_id: str, provider: str) -> bool:
    token = get_token(user_id, provider)
    if not token:
        return False
    if token.expires_at and time.time() > token.expires_at - 60:
        return False
    return True


# ── OAuth PKCE Flow ──────────────────────────────────────────────────────

def start_auth_flow(provider: str, user_id: str) -> dict:
    """Start OAuth PKCE flow. Returns auth URL for the user to visit."""
    config = PROVIDERS.get(provider)
    if not config:
        return {"error": f"unknown provider: {provider}"}

    if config.get("type") == "device_flow":
        return {"error": f"use /auth/{provider}/device-start for device flow providers"}

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

    await store_token_async(user_id, provider, token)
    logger.info("oauth_token_stored", extra={"provider": provider, "user_id": user_id})
    return token


# ── GitHub Copilot Device Flow ───────────────────────────────────────────

async def start_device_flow(user_id: str) -> dict:
    """Start GitHub Device Flow — returns user_code and verification_uri."""
    config = PROVIDERS["github-copilot"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            config["device_url"],
            data={
                "client_id": config["client_id"],
                "scope": config["scope"],
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )

    if resp.status_code != 200:
        logger.error("device_flow_start_failed", extra={"status": resp.status_code, "body": resp.text[:200]})
        return {"error": "failed to start device flow", "status": resp.status_code}

    data = resp.json()
    device_code = data["device_code"]
    interval = data.get("interval", 5)

    _device_flow_status[user_id] = {
        "status": "pending",
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "expires_in": data.get("expires_in", 900),
        "started_at": time.time(),
    }

    # Start background polling
    asyncio.create_task(_poll_device_token(user_id, device_code, interval))

    return {
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "expires_in": data.get("expires_in", 900),
        "interval": interval,
    }


async def _poll_device_token(user_id: str, device_code: str, interval: int) -> None:
    """Background polling for device flow token."""
    config = PROVIDERS["github-copilot"]
    deadline = time.time() + 900  # 15 min max

    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            await asyncio.sleep(interval)

            try:
                resp = await client.post(
                    config["token_url"],
                    data={
                        "client_id": config["client_id"],
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                    timeout=15.0,
                )
            except Exception as exc:
                logger.warning("device_poll_request_failed", extra={"error": str(exc)[:200]})
                continue

            data = resp.json()
            error = data.get("error")

            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error == "expired_token":
                _device_flow_status[user_id] = {"status": "expired"}
                return
            elif error == "access_denied":
                _device_flow_status[user_id] = {"status": "denied"}
                return
            elif error:
                _device_flow_status[user_id] = {"status": "error", "error": error}
                return

            # Success — got GitHub access token
            github_token = data.get("access_token", "")
            logger.info("device_flow_github_token_acquired", extra={"user_id": user_id})

            # Exchange for Copilot token
            copilot_token_data = await _exchange_copilot_token(client, github_token)
            if not copilot_token_data:
                _device_flow_status[user_id] = {"status": "error", "error": "copilot_token_exchange_failed"}
                return

            token = OAuthToken(
                access_token=copilot_token_data["token"],
                expires_at=copilot_token_data.get("expires_at", time.time() + 1800),
                provider="github-copilot",
                user_id=user_id,
                github_access_token=github_token,
            )

            await store_token_async(user_id, "github-copilot", token)
            _device_flow_status[user_id] = {"status": "authenticated"}
            logger.info("copilot_token_stored", extra={"user_id": user_id})

            # Schedule periodic copilot token refresh
            asyncio.create_task(_refresh_copilot_token_loop(user_id))
            return

    # Timed out
    _device_flow_status[user_id] = {"status": "expired"}


async def _exchange_copilot_token(client: httpx.AsyncClient, github_token: str) -> dict | None:
    """Exchange GitHub access_token for Copilot internal token."""
    config = PROVIDERS["github-copilot"]
    try:
        resp = await client.get(
            config["copilot_token_url"],
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.error("copilot_token_exchange_failed", extra={"status": resp.status_code, "body": resp.text[:200]})
    except Exception as exc:
        logger.error("copilot_token_exchange_error", extra={"error": str(exc)[:200]})
    return None


async def _refresh_copilot_token_loop(user_id: str) -> None:
    """Refresh Copilot token every 30 minutes using stored GitHub access_token."""
    while True:
        await asyncio.sleep(1800)  # 30분
        token = await get_token_async(user_id, "github-copilot")
        if not token or not token.github_access_token:
            logger.info("copilot_refresh_stopped_no_token", extra={"user_id": user_id})
            return

        async with httpx.AsyncClient() as client:
            copilot_data = await _exchange_copilot_token(client, token.github_access_token)

        if not copilot_data:
            logger.warning("copilot_refresh_failed", extra={"user_id": user_id})
            continue

        token.access_token = copilot_data["token"]
        token.expires_at = copilot_data.get("expires_at", time.time() + 1800)
        await store_token_async(user_id, "github-copilot", token)
        logger.info("copilot_token_refreshed", extra={"user_id": user_id})


async def get_device_flow_status(user_id: str) -> dict:
    """Return current device flow status for user."""
    status = _device_flow_status.get(user_id)
    if not status:
        return {"status": "not_started"}
    return status


# ── LLM Call with OAuth Token ────────────────────────────────────────────

def call_with_oauth(user_id: str, provider: str, messages: list[dict], model: str = "", max_tokens: int = 500) -> str | None:
    """Call LLM using user's OAuth token."""
    token = get_token(user_id, provider)
    if not token or not token.access_token:
        return None

    if provider == "claude":
        return _call_claude_api(token.access_token, messages, model or "claude-sonnet-4-5-20250929", max_tokens)
    elif provider == "codex":
        return _call_openai_api(token.access_token, messages, model or "gpt-4o", max_tokens)
    elif provider == "github-copilot":
        return _call_github_copilot_api(token.access_token, messages, model or "gpt-4o", max_tokens)
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


def _call_github_copilot_api(copilot_token: str, messages: list[dict], model: str, max_tokens: int) -> str | None:
    """Call GitHub Copilot Chat Completions API (OpenAI-compatible)."""
    config = PROVIDERS["github-copilot"]
    try:
        resp = httpx.post(
            config["chat_url"],
            headers={
                "Authorization": f"Bearer {copilot_token}",
                "Content-Type": "application/json",
                "Editor-Version": "vscode/1.85.0",
                "Copilot-Integration-Id": "vscode-chat",
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
        logger.warning("copilot_api_failed", extra={"error": str(exc)[:200]})
    return None
