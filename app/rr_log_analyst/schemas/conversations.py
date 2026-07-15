from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DisplayMessage(BaseModel):
    role: str
    content: str
    message_time: str | None = None
    reasoning: str | None = None
    seq: int | None = None


class TranscriptToolCall(BaseModel):
    id: str | None = None
    name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class TranscriptMessage(BaseModel):
    seq: int
    role: str  # "human" | "ai" | "tool"
    status: str  # "complete" | "partial"
    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[TranscriptToolCall] | None = None  # ai turns that invoked tools
    tool_call_id: str | None = None  # tool turns: which call this result answers
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None
    message_time: str | None = None
    created_at: datetime


class ConversationTranscript(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    is_internal: bool = False
    messages: list[TranscriptMessage] = Field(default_factory=list)


class ConversationCreateRequest(BaseModel):
    user_id: int = Field(..., description="Clickpost dashboard user id")
    enterprise_id: int = Field(..., description="Clickpost enterprise id")
    internal_user_id :str | None = Field(None, description="Clickpost internal user id")
    is_internal_user: bool = Field(..., description="Is the user an internal user") 


class ConversationCreateResponse(BaseModel):
    id: str
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    title: str | None = None
    preview: str | None = None


class ConversationDetail(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    messages: list[DisplayMessage] = Field(default_factory=list)
