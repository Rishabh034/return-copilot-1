from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field


class GrantAdminRequest(BaseModel):
    user_id: int = Field(
        ..., gt=0, description="Clickpost dashboard user id — the signed JWT claim"
    )
    role: Literal["viewer", "manager"] = Field(
        "viewer",
        description=(
            "viewer reads the dashboard; manager also changes caps, enforcement "
            "and this list. Defaults to viewer — the smaller grant."
        ),
    )
    internal_user_id: str | None = Field(
        None,
        max_length=64,
        description=(
            "Optional. Required for the shared internal-staff account, where one "
            "Django user fronts many people. Null grants on the signed user_id alone."
        ),
    )
    note: str | None = Field(
        None,
        max_length=200,
        description="Who this is, for the audit trail — e.g. 'siddhant · platform'",
    )


class AdminResponse(BaseModel):
    user_id: int
    internal_user_id: str | None
    role: str
    note: str | None
    granted_by: int
    created_at: datetime
    revoked_at: datetime | None
    is_active: bool


class AdminListResponse(BaseModel):
    admins: list[AdminResponse]
    active_count: int
    manager_count: int
