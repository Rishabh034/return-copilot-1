"""Append and read the admin audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rr_log_analyst.models.admin_audit_log import AdminAuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor_user_id: int,
        actor_internal_user_id: str | None,
        action: str,
        target_type: str,
        target_id: str | int,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AdminAuditLog:
        """Append one entry. Flushed, not committed - the caller owns the transaction.

        Deliberately part of the same transaction as the change it describes, so
        a write can never succeed without its audit row.
        """
        row = AdminAuditLog(
            id=uuid.uuid4(),
            actor_user_id=actor_user_id,
            actor_internal_user_id=actor_internal_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            before=before,
            after=after,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list(
        self,
        *,
        frm: datetime | None = None,
        to: datetime | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        actor_internal_user_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AdminAuditLog]:
        clauses = []
        if frm is not None:
            clauses.append(AdminAuditLog.created_at >= frm)
        if to is not None:
            clauses.append(AdminAuditLog.created_at < to)
        if target_type:
            clauses.append(AdminAuditLog.target_type == target_type)
        if target_id:
            clauses.append(AdminAuditLog.target_id == str(target_id))
        if actor_internal_user_id:
            clauses.append(
                AdminAuditLog.actor_internal_user_id == actor_internal_user_id
            )
        if action:
            clauses.append(AdminAuditLog.action == action)

        statement = select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())
        if clauses:
            statement = statement.where(and_(*clauses))
        result = await self._session.execute(statement.limit(limit))
        return list(result.scalars().all())
