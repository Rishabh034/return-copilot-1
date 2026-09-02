from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentAdmin(Base):
    """Allowlist of ClickPost staff who may open the internal dashboard.

    A grant is identified by the pair (user_id, internal_user_id).

    `user_id` is a **signed** claim in the agent JWT and is always required.
    `internal_user_id` is optional and is *not* signed - it arrives from the
    caller - so it can never authorize on its own. Requiring both means an
    outsider cannot reach the dashboard at all: they would first have to hold a
    token whose signed user_id is already on the list.

    Two shapes of grant:

    * `internal_user_id IS NULL` - a plain user_id grant, authorized entirely by
      the signed claim. The stronger of the two, and correct for a genuinely
      distinct Django user.
    * `internal_user_id` set - for the shared internal-staff account, where one
      Django user fronts many people and the signed claim alone cannot tell them
      apart. Both values must match.

    The second shape is a deliberate compromise, not the end state: once
    clickpost adds internal_user_id to the JWT claims (ticket F3) the guard can
    read it from the verified token and the distinction disappears.

    Access is revoked by setting `revoked_at`, never by deleting the row - who
    had access to customer spend data, and when, is worth being able to answer
    later.
    """

    __tablename__ = "agent_admin"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Null means "authorize on the signed user_id alone".
    internal_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # viewer reads the dashboard; manager also changes caps, enforcement and
    # this table. Reading customer spend is far lower risk than changing it.
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manager", server_default="manager"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    VIEWER = "viewer"
    MANAGER = "manager"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @property
    def can_manage(self) -> bool:
        return self.is_active and self.role == self.MANAGER
