"""Who may open the internal dashboard."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.rr_log_analyst.models.agent_admin import AgentAdmin


class AgentAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_grant(
        self, user_id: int, internal_user_id: str | None = None
    ) -> AgentAdmin | None:
        """The active grant matching this caller, or None.

        Returns the row rather than a boolean so callers can distinguish viewer
        from manager without a second query.
        """
        clauses = [AgentAdmin.user_id == user_id, AgentAdmin.revoked_at.is_(None)]
        if internal_user_id:
            match = or_(
                AgentAdmin.internal_user_id.is_(None),
                AgentAdmin.internal_user_id == internal_user_id,
            )
        else:
            match = AgentAdmin.internal_user_id.is_(None)

        result = await self._session.execute(
            select(AgentAdmin)
            .where(*clauses, match)
            # A specific grant outranks a blanket one, so a person given viewer
            # is not silently promoted by a user_id-wide manager row.
            .order_by(AgentAdmin.internal_user_id.desc().nullslast())
        )
        return result.scalars().first()

    async def is_admin(self, user_id: int, internal_user_id: str | None = None) -> bool:
        """Both halves must match, and the signed half is never optional.

        A row with a NULL internal_user_id authorizes on the signed `user_id`
        alone. A row with one set requires the caller to present that exact
        value *as well as* holding a token for the same user_id - so the
        unsigned field narrows access, it never grants it.
        """
        return await self.find_grant(user_id, internal_user_id) is not None

    async def list_all(self, *, include_revoked: bool = False) -> list[AgentAdmin]:
        statement = select(AgentAdmin).order_by(AgentAdmin.created_at)
        if not include_revoked:
            statement = statement.where(AgentAdmin.revoked_at.is_(None))
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def _find(
        self, *, user_id: int, internal_user_id: str | None
    ) -> AgentAdmin | None:
        result = await self._session.execute(
            select(AgentAdmin).where(
                AgentAdmin.user_id == user_id,
                AgentAdmin.internal_user_id.is_(None)
                if internal_user_id is None
                else AgentAdmin.internal_user_id == internal_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def grant(
        self,
        *,
        user_id: int,
        granted_by: int,
        internal_user_id: str | None = None,
        role: str = "manager",
        note: str | None = None,
    ) -> AgentAdmin:
        """Add an admin, or reinstate one whose access was revoked.

        Reinstating rather than inserting preserves the original `created_at`,
        so the row reads as one person's access history rather than a fresh
        grant with no past.
        """
        existing = await self._find(user_id=user_id, internal_user_id=internal_user_id)
        if existing is not None:
            existing.revoked_at = None
            existing.granted_by = granted_by
            existing.role = role
            if note is not None:
                existing.note = note
            await self._session.flush()
            return existing

        row = AgentAdmin(
            user_id=user_id,
            internal_user_id=internal_user_id,
            role=role,
            note=note,
            granted_by=granted_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def revoke(self, *, user_id: int, internal_user_id: str | None = None) -> bool:
        """Soft revoke one grant. False when there was nothing active to remove."""
        result = await self._session.execute(
            update(AgentAdmin)
            .where(
                AgentAdmin.user_id == user_id,
                AgentAdmin.internal_user_id.is_(None)
                if internal_user_id is None
                else AgentAdmin.internal_user_id == internal_user_id,
                AgentAdmin.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def count_active(self, *, role: str | None = None) -> int:
        statement = select(AgentAdmin.id).where(AgentAdmin.revoked_at.is_(None))
        if role:
            statement = statement.where(AgentAdmin.role == role)
        result = await self._session.execute(statement)
        return len(result.scalars().all())
