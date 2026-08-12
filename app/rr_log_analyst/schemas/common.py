from pydantic import BaseModel,Field


class ExtraTokensInput(BaseModel):
    enterprise_id: int = Field(..., description="Clickpost enterprise id")
    enterprise_name: str | None = Field(None, description="Clickpost enterprise name")
    user_id: int = Field(..., description="Clickpost dashboard user id")
    internal_user_id :str | None = Field(None, description="Clickpost internal user id")
    is_internal_user: bool = Field(False, description="Is the user an internal user")    