from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SubmitFeedbackRequest(BaseModel):
    conversation_id: str = Field(..., description="Conversation the turn belongs to")
    seq: int = Field(
        ...,
        ge=0,
        description="Turn anchor from the stream's `done` event, sent back verbatim",
    )
    enterprise_id: int = Field(..., description="Clickpost enterprise id")
    user_id: int = Field(..., description="Clickpost dashboard user id")
    positive: bool = Field(
        ..., description="Thumbs up (true) or thumbs down (false) on the assistant turn"
    )
    comment: str | None = Field(default=None, max_length=10_000)


class FeedbackResponse(BaseModel):
    id: str
    conversation_id: str
    seq: int
    positive: bool
    comment: str | None
    created_at: datetime
