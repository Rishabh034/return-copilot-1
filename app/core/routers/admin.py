"""Manage the ClickPost admin allowlist.

Every route here is gated by `require_clickpost_admin`, including the ones that
change the list - admins add and remove admins. The first row therefore has to
be inserted by hand (see the d4e2b7a91c58 migration); a bootstrap path in config
would be a permanent way to bypass the check.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.core.auth.admin import require_clickpost_admin, require_clickpost_staff
from app.core.auth.agent_token import AuthContext
from app.core.admin.admin_audit_log_model import AdminAuditLog
from app.core.admin.agent_admin_model import AgentAdmin
from app.core.admin.audit_repo import AuditRepository
from app.core.admin.admin_repo import AgentAdminRepository
from app.core.admin.admin_schema import (
    AdminListResponse,
    AdminResponse,
    GrantAdminRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_response(row: AgentAdmin) -> AdminResponse:
    return AdminResponse(
        user_id=row.user_id,
        internal_user_id=row.internal_user_id,
        role=row.role,
        note=row.note,
        granted_by=row.granted_by,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        is_active=row.is_active,
    )


@router.get("/admins", response_model=AdminListResponse)
async def list_admins(
    include_revoked: bool = False,
    _: AuthContext = Depends(require_clickpost_staff),
    session: AsyncSession = Depends(get_db),
):
    """Who can see cross-enterprise spend.

    `include_revoked=true` returns the full history rather than the current
    list, which is the form you want when answering "who had access in March".
    """
    repo = AgentAdminRepository(session)
    rows = await repo.list_all(include_revoked=include_revoked)
    return AdminListResponse(
        admins=[_to_response(r) for r in rows],
        active_count=sum(1 for r in rows if r.is_active),
        manager_count=sum(1 for r in rows if r.can_manage),
    )


@router.post("/admins", response_model=AdminResponse, status_code=201)
async def grant_admin(
    body: GrantAdminRequest,
    caller: AuthContext = Depends(require_clickpost_admin),
    session: AsyncSession = Depends(get_db),
):
    """Grant admin access, or reinstate a revoked grant."""
    repo = AgentAdminRepository(session)
    row = await repo.grant(
        user_id=body.user_id,
        internal_user_id=body.internal_user_id,
        role=body.role,
        granted_by=caller.user_id,
        note=body.note,
    )
    await AuditRepository(session).record(
        actor_user_id=caller.user_id,
        actor_internal_user_id=caller.internal_user_id,
        action=AdminAuditLog.ADMIN_GRANTED,
        target_type=AdminAuditLog.TARGET_ADMIN,
        target_id=f"{body.user_id}/{body.internal_user_id or ''}",
        after={"role": row.role, "note": body.note},
    )
    await session.commit()

    logger.info(
        "Admin access granted user_id=%s internal_user_id=%r by=%s note=%r",
        body.user_id,
        body.internal_user_id,
        caller.user_id,
        body.note,
    )
    return _to_response(row)


@router.delete("/admins/{user_id}", response_model=AdminResponse)
async def revoke_admin(
    user_id: int,
    internal_user_id: str | None = None,
    caller: AuthContext = Depends(require_clickpost_admin),
    x_internal_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Revoke one grant. Soft delete — the row stays for the audit trail.

    Pass `internal_user_id` to target a specific person on a shared account;
    omit it to revoke the plain user_id grant.
    """
    repo = AgentAdminRepository(session)
    target = (internal_user_id or "").strip() or None
    caller_internal = (x_internal_user_id or "").strip() or None

    # Locking everyone out is recoverable only by hand-editing the database, so
    # refuse the two ways it happens: removing yourself, or removing the last
    # grant standing.
    if user_id == caller.user_id and target == caller_internal:
        raise HTTPException(
            status_code=409,
            detail="You cannot revoke your own admin access. Ask another admin.",
        )
    # Only managers can restore access, so the last manager standing is the one
    # that must not be removable.
    if await repo.count_active(role=AgentAdmin.MANAGER) <= 1:
        raise HTTPException(
            status_code=409,
            detail="Cannot revoke the last remaining manager.",
        )

    row = await repo._find(user_id=user_id, internal_user_id=target)
    if row is None or not await repo.revoke(user_id=user_id, internal_user_id=target):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active admin grant for user_id {user_id}"
                + (f" / internal_user_id {target}" if target else "")
            ),
        )
    await AuditRepository(session).record(
        actor_user_id=caller.user_id,
        actor_internal_user_id=caller.internal_user_id,
        action=AdminAuditLog.ADMIN_REVOKED,
        target_type=AdminAuditLog.TARGET_ADMIN,
        target_id=f"{user_id}/{target or ''}",
        before={"role": row.role},
    )
    await session.commit()

    logger.info(
        "Admin access revoked user_id=%s internal_user_id=%r by=%s",
        user_id, target, caller.user_id,
    )
    await session.refresh(row)
    return _to_response(row)
