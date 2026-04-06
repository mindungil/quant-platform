"""Conversation History — PostgreSQL 기반 대화 기록 저장/조회.

테이블:
  conversations: 대화 세션
  chat_messages: 메시지 (user, assistant, tool 역할)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.persistence import SqlStore
from app.core.config import settings

logger = logging.getLogger("llm-gateway")

_store: SqlStore | None = None


def _get_store() -> SqlStore:
    global _store
    if _store is None:
        _store = SqlStore(settings.postgres_url)
    return _store


async def ensure_tables() -> None:
    """Create tables if not exist."""
    store = _get_store()
    store.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    store.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tool_calls JSONB,
            tool_name TEXT,
            tool_call_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    store.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
        ON chat_messages(conversation_id, created_at)
    """)
    store.execute("""
        CREATE INDEX IF NOT EXISTS idx_conversations_user
        ON conversations(user_id, updated_at DESC)
    """)


def create_conversation(user_id: str, title: str = "") -> dict:
    """Create new conversation session."""
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    store = _get_store()
    store.execute(
        "INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at) VALUES (:cid, :uid, :title, :ts, :ts)",
        {"cid": conv_id, "uid": user_id, "title": title, "ts": now},
    )
    return {"conversation_id": conv_id, "user_id": user_id, "title": title, "created_at": now.isoformat()}


def list_conversations(user_id: str, limit: int = 20) -> list[dict]:
    """List user's conversations (newest first)."""
    store = _get_store()
    rows = store.fetch_all(
        "SELECT conversation_id, user_id, title, created_at, updated_at FROM conversations WHERE user_id = :uid ORDER BY updated_at DESC LIMIT :lim",
        {"uid": user_id, "lim": limit},
    )
    return [
        {
            "conversation_id": r["conversation_id"],
            "user_id": r["user_id"],
            "title": r["title"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
        }
        for r in rows
    ]


def get_conversation(conversation_id: str) -> dict | None:
    store = _get_store()
    row = store.fetch_one(
        "SELECT conversation_id, user_id, title, created_at, updated_at FROM conversations WHERE conversation_id = :cid",
        {"cid": conversation_id},
    )
    if not row:
        return None
    return {
        "conversation_id": row["conversation_id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def save_message(
    conversation_id: str,
    role: str,
    content: str = "",
    tool_calls: list[dict] | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> dict:
    """Save a message to conversation history."""
    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    store = _get_store()

    tc_json = json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
    store.execute(
        """INSERT INTO chat_messages
           (message_id, conversation_id, role, content, tool_calls, tool_name, tool_call_id, created_at)
           VALUES (:mid, :cid, :role, :content, :tc, :tn, :tcid, :ts)""",
        {"mid": msg_id, "cid": conversation_id, "role": role, "content": content, "tc": tc_json, "tn": tool_name, "tcid": tool_call_id, "ts": now},
    )
    # Update conversation timestamp
    store.execute(
        "UPDATE conversations SET updated_at = :ts WHERE conversation_id = :cid",
        {"ts": now, "cid": conversation_id},
    )
    return {"message_id": msg_id, "role": role, "created_at": now.isoformat()}


def get_messages(conversation_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation (oldest first, for LLM context)."""
    store = _get_store()
    rows = store.fetch_all(
        """SELECT message_id, role, content, tool_calls, tool_name, tool_call_id, created_at
           FROM chat_messages
           WHERE conversation_id = :cid
           ORDER BY created_at ASC
           LIMIT :lim""",
        {"cid": conversation_id, "lim": limit},
    )
    messages = []
    for r in rows:
        msg: dict[str, Any] = {
            "message_id": r["message_id"],
            "role": r["role"],
            "content": r["content"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        if r.get("tool_calls"):
            tc = r["tool_calls"]
            msg["tool_calls"] = json.loads(tc) if isinstance(tc, str) else tc
        if r.get("tool_name"):
            msg["tool_name"] = r["tool_name"]
        if r.get("tool_call_id"):
            msg["tool_call_id"] = r["tool_call_id"]
        messages.append(msg)
    return messages


def get_llm_context(conversation_id: str, max_messages: int = 30) -> list[dict]:
    """Get messages formatted for LLM context (simplified role/content pairs).

    Returns only the most recent messages to fit within context limits.
    Skips tool-level detail, keeping user and assistant messages.
    """
    store = _get_store()
    rows = store.fetch_all(
        """SELECT role, content FROM chat_messages
           WHERE conversation_id = :cid AND role IN ('user', 'assistant')
           ORDER BY created_at DESC
           LIMIT :lim""",
        {"cid": conversation_id, "lim": max_messages},
    )
    # Reverse to chronological order
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows) if r.get("content")]


def update_conversation_title(conversation_id: str, title: str) -> None:
    store = _get_store()
    store.execute(
        "UPDATE conversations SET title = :title WHERE conversation_id = :cid",
        {"title": title, "cid": conversation_id},
    )
