from pydantic import BaseModel, Field


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000)
    conversation_id: str | None = None
    enterprise_id: int = Field(..., description="Clickpost enterprise id")
    enterprise_name: str | None = Field(None, description="Clickpost enterprise name")
    username : str | None = Field(None, description="Clickpost dashboard username")
    user_id: int = Field(..., description="Clickpost dashboard user id")
    message_time: str | None = None
