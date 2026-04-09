"""Chat API Pydantic Models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None  # None → 새 대화 생성


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    result: str | None = None
    error: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    text: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    provider: str = ""
    loop_count: int = 0
    elapsed_ms: float = 0.0


class ConversationSummary(BaseModel):
    conversation_id: str
    user_id: str
    title: str
    created_at: str | None = None
    updated_at: str | None = None


class MessageRecord(BaseModel):
    message_id: str
    role: str
    content: str
    tool_calls: list[dict] | None = None
    tool_name: str | None = None
    created_at: str | None = None
