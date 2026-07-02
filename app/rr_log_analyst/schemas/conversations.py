from datetime import datetime

from pydantic import BaseModel, Field


class DisplayMessage(BaseModel):
    role: str
    content: str
    message_time: str | None = None
    reasoning: str | None = None
    seq: int | None = None


class ConversationCreateRequest(BaseModel):
    user_id: int = Field(..., description="Clickpost dashboard user id")
    enterprise_id: int = Field(..., description="Clickpost enterprise id")


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
