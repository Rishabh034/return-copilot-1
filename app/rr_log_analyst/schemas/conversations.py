from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CreateConversationResponse(BaseModel):
    conversation_id: UUID
    title: str | None = None


class ConversationSummary(BaseModel):
    id: UUID
    title: str | None
    model: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: UUID
    seq: int
    role: str
    content: str | None = None
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: dict | None = None
    created_at: datetime
