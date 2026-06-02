from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000)
