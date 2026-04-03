"""LLM Providers — OAuth 구독 기반 (OpenCode 방식 포팅).

지원 프로바이더:
1. GitHub Copilot (OAuth 토큰 → Bearer 토큰 교환)
2. Claude Code CLI (이미 인증된 세션 활용)
3. API Key fallback (OpenAI/Anthropic 직접)

Reference: opencode-ai/opencode의 copilot.go provider 로직을 Python으로 포팅.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger("llm-gateway")


class CopilotProvider:
    """GitHub Copilot 구독 기반 LLM 프로바이더.

    GitHub OAuth 토큰을 Copilot Bearer 토큰으로 교환 후
    api.githubcopilot.com에 OpenAI 호환 API로 호출.

    Ported from: opencode-ai/opencode/internal/llm/provider/copilot.go
    """

    BASE_URL = "https://api.githubcopilot.com"
    TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"

    def __init__(self) -> None:
        self._bearer_token: str | None = None
        self._token_expires_at: float = 0
        self._github_token: str | None = self._load_github_token()

    def _load_github_token(self) -> str | None:
        """Load GitHub OAuth token from standard locations."""
        # 1. Environment variable
        token = os.getenv("GITHUB_TOKEN")
        if token:
            return token

        # 2. GitHub CLI config
        hosts_path = Path.home() / ".config" / "gh" / "hosts.yml"
        if hosts_path.exists():
            try:
                import yaml
                with open(hosts_path) as f:
                    hosts = yaml.safe_load(f)
                    if hosts and "github.com" in hosts:
                        token = hosts["github.com"].get("oauth_token")
                        if token:
                            return token
            except Exception:
                pass

        # 3. GitHub Copilot config
        copilot_path = Path.home() / ".config" / "github-copilot" / "hosts.json"
        if copilot_path.exists():
            try:
                with open(copilot_path) as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if "github.com" in key and "oauth_token" in value:
                            return value["oauth_token"]
            except Exception:
                pass

        return None

    def _exchange_token(self) -> str | None:
        """Exchange GitHub token for Copilot bearer token."""
        if not self._github_token:
            return None

        try:
            resp = httpx.get(
                self.TOKEN_EXCHANGE_URL,
                headers={
                    "Authorization": f"Token {self._github_token}",
                    "User-Agent": "QuantPlatform/1.0",
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._bearer_token = data.get("token")
                self._token_expires_at = data.get("expires_at", 0)
                return self._bearer_token
        except Exception as exc:
            logger.warning("copilot_token_exchange_failed", extra={"error": str(exc)[:100]})
        return None

    def _ensure_token(self) -> str | None:
        if self._bearer_token and time.time() < self._token_expires_at - 60:
            return self._bearer_token
        return self._exchange_token()

    def available(self) -> bool:
        return self._github_token is not None

    def call(self, messages: list[dict], model: str = "gpt-4o", max_tokens: int = 500) -> str | None:
        token = self._ensure_token()
        if not token:
            return None

        try:
            resp = httpx.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Editor-Version": "QuantPlatform/1.0",
                    "Editor-Plugin-Version": "QuantPlatform/1.0",
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
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.warning("copilot_call_failed", extra={"error": str(exc)[:100]})
        return None


class ClaudeCliProvider:
    """Claude Code CLI 기반 프로바이더.

    이미 인증된 Claude Code 세션을 `claude --print`로 활용.
    """

    def available(self) -> bool:
        try:
            result = subprocess.run(["claude", "--version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def call(self, messages: list[dict], **kwargs) -> str | None:
        prompt_parts = []
        for msg in messages:
            if msg["role"] == "system":
                prompt_parts.append(f"[시스템 지시] {msg['content']}")
            else:
                prompt_parts.append(msg["content"])

        prompt = "\n\n".join(prompt_parts)
        try:
            result = subprocess.run(
                ["claude", "--print", prompt],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL": "1"},
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception as exc:
            logger.warning("claude_cli_failed", extra={"error": str(exc)[:100]})
        return None


class ApiKeyProvider:
    """공식 API 키 기반 프로바이더 (LiteLLM)."""

    def __init__(self) -> None:
        self._api_key = os.getenv("OPENAI_API_KEY", os.getenv("GRAPHRAG_API_KEY", ""))
        self._anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    def available(self) -> bool:
        return bool(self._api_key or self._anthropic_key)

    def call(self, messages: list[dict], model: str = "gpt-4o-mini", max_tokens: int = 500) -> str | None:
        if not self.available():
            return None
        try:
            import litellm
            response = litellm.completion(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("litellm_failed", extra={"error": str(exc)[:200]})
        return None


# Provider chain — try in order, first success wins
_copilot = CopilotProvider()
_claude_cli = ClaudeCliProvider()
_api_key = ApiKeyProvider()


def get_available_providers() -> list[str]:
    available = []
    if _copilot.available():
        available.append("copilot")
    if _claude_cli.available():
        available.append("claude-cli")
    if _api_key.available():
        available.append("api-key")
    return available


def call_llm(messages: list[dict], model: str = "gpt-4o-mini", max_tokens: int = 500) -> tuple[str | None, str]:
    """Call LLM using the first available provider.

    Returns (response_text, provider_name).
    """
    # 1. GitHub Copilot (OAuth subscription)
    if _copilot.available():
        result = _copilot.call(messages, model=model, max_tokens=max_tokens)
        if result:
            return result, "copilot/subscription"

    # 2. Claude Code CLI (authenticated session)
    if _claude_cli.available():
        result = _claude_cli.call(messages)
        if result:
            return result, "claude-code/subscription"

    # 3. API Key (LiteLLM)
    if _api_key.available():
        result = _api_key.call(messages, model=model, max_tokens=max_tokens)
        if result:
            return result, f"litellm/{model}"

    return None, "none"
