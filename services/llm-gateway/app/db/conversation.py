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
        "INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
        (conv_id, user_id, title, now, now),
    )
    return {"conversation_id": conv_id, "user_id": user_id, "title": title, "created_at": now.isoformat()}


def list_conversations(user_id: str, limit: int = 20) -> list[dict]:
    """List user's conversations (newest first)."""
    store = _get_store()
    rows = store.fetch_all(
        "SELECT conversation_id, user_id, title, created_at, updated_at FROM conversations WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
        (user_id, limit),
    )
    return [
        {
            "conversation_id": r[0],
            "user_id": r[1],
            "title": r[2],
            "created_at": r[3].isoformat() if r[3] else None,
            "updated_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


def get_conversation(conversation_id: str) -> dict | None:
    store = _get_store()
    row = store.fetch_one(
        "SELECT conversation_id, user_id, title, created_at, updated_at FROM conversations WHERE conversation_id = %s",
        (conversation_id,),
    )
    if not row:
        return None
    return {
        "conversation_id": row[0],
        "user_id": row[1],
        "title": row[2],
        "created_at": row[3].isoformat() if row[3] else None,
        "updated_at": row[4].isoformat() if row[4] else None,
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
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (msg_id, conversation_id, role, content, tc_json, tool_name, tool_call_id, now),
    )
    # Update conversation timestamp
    store.execute(
        "UPDATE conversations SET updated_at = %s WHERE conversation_id = %s",
        (now, conversation_id),
    )
    return {"message_id": msg_id, "role": role, "created_at": now.isoformat()}


def get_messages(conversation_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation (oldest first, for LLM context)."""
    store = _get_store()
    rows = store.fetch_all(
        """SELECT message_id, role, content, tool_calls, tool_name, tool_call_id, created_at
           FROM chat_messages
           WHERE conversation_id = %s
           ORDER BY created_at ASC
           LIMIT %s""",
        (conversation_id, limit),
    )
    messages = []
    for r in rows:
        msg: dict[str, Any] = {
            "message_id": r[0],
            "role": r[1],
            "content": r[2],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        if r[3]:
            msg["tool_calls"] = json.loads(r[3]) if isinstance(r[3], str) else r[3]
        if r[4]:
            msg["tool_name"] = r[4]
        if r[5]:
            msg["tool_call_id"] = r[5]
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
           WHERE conversation_id = %s AND role IN ('user', 'assistant')
           ORDER BY created_at DESC
           LIMIT %s""",
        (conversation_id, max_messages),
    )
    # Reverse to chronological order
    return [{"role": r[0], "content": r[1]} for r in reversed(rows) if r[1]]


def update_conversation_title(conversation_id: str, title: str) -> None:
    store = _get_store()
    store.execute(
        "UPDATE conversations SET title = %s WHERE conversation_id = %s",
        (title, conversation_id),
    )
