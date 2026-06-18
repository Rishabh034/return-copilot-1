from datetime import datetime

from pydantic import BaseModel, Field


class DisplayMessage(BaseModel):
    role: str
    content: str
    message_time: str | None = None
    reasoning: str | None = None


class ConversationCreateResponse(BaseModel):
    id: str
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    preview: str | None = None


class ConversationDetail(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    messages: list[DisplayMessage] = Field(default_factory=list)
