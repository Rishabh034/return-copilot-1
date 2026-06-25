from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000)
    conversation_id: str | None = None
    enterprise_id: int | None = None
    message_time : str | None = None

