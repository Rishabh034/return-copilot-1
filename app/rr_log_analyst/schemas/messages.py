from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000)
    conversation_id: str | None = None
    token: str | None = Field(
        default=None,
        description="Optional Clickpost API token for local chat UI",
    )
