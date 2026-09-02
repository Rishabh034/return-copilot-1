"""Authorization for the internal budget dashboard.

Two different things get called "admin" in this product and they must not be
conflated:

  * An **enterprise admin** works at a customer and may allocate their own
    company's budget. Only Clickpost knows who that is, so it will arrive as a
    role claim in the agent JWT - ticket F1.
  * A **ClickPost staff member** may look at every enterprise's spend. That is
    what this module gates.

Within ClickPost there are two levels, because the risks differ sharply:

  * **viewer** - read the dashboard. Granted by an allowlist row, or - only when
    `budget_dashboard_open_to_all_staff` is on - by a verified staff claim.
    That setting is off by default: clickpost marks every internal account
    is_staff, so it means the entire company, not a team.
  * **manager** - change a cap, flip enforcement, grant admin access. High risk,
    and deliberately a short list.

## Why staffness cannot simply be checked

ClickPost staff reach an enterprise through InternalCustomAuthTokenView, which
validates `is_staff` and then issues a Token for the *enterprise's* user. By the
time the agent JWT is minted, `request.user.id` is the enterprise owner - id 14
for the Demo enterprise, whose own `is_staff` is false. The staff identity is
lost at that exchange.

So this module reads staffness from a signed `is_staff` claim when clickpost
provides one, and otherwise falls back to an explicit allowlist row. The
fallback is not a lesser check - it is the stronger one - but it means a staff
member must be listed before they can read anything. Adding `is_staff` and
`internal_user_id` to the claims in selector/views/ai_agent_token_view.py, using
the same validation InternalCustomAuthTokenView already performs, removes that
requirement for viewers. Ticket F3.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.dependencies.auth import AgentAuthError, AuthContext, require_auth
from app.rr_log_analyst.models.agent_admin import AgentAdmin
from app.rr_log_analyst.repositories.admin_repo import AgentAdminRepository

logger = logging.getLogger(__name__)


async def _authenticate(authorization: str | None) -> AuthContext:
    """Verify the token, refusing outright when verification is switched off.

    Unlike the chat endpoints there is no `agent_jwt_enabled = false` fallback.
    That switch lets local development pass identity in the request body, which
    is fine for "whose conversation is this" and unacceptable for "may this
    person read every customer's spend".
    """
    if not settings.agent_jwt_enabled:
        # AgentAuthError, not HTTPException: this service sits behind a caller
        # that treats a non-200 as a transport failure and discards the body, so
        # a real 503 would reach the client as an empty error. Every auth
        # failure on this surface therefore uses the same envelope - HTTP 200
        # carrying the true status in `meta.status` - and clients read that
        # rather than the HTTP code.
        raise AgentAuthError(
            "Admin endpoints require agent JWT verification "
            "(AGENT_JWT_ENABLED=true). Identity cannot be trusted without it.",
            status_code=503,
        )
    return await require_auth(authorization=authorization)


def _caller_internal_id(ctx: AuthContext, header_value: str | None) -> str | None:
    """Prefer the signed claim; fall back to the header.

    Once clickpost signs `internal_user_id` the header is ignored entirely, so
    this upgrades itself the moment that ships - no change needed here.
    """
    if ctx.internal_user_id:
        return ctx.internal_user_id
    return (header_value or "").strip() or None


async def require_clickpost_staff(
    authorization: str | None = Header(default=None),
    x_internal_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Read access to the budget dashboard.

    Granted by a signed `is_staff` claim, or by any active allowlist row. The
    header alone never grants anything: without the signed claim a caller must
    already be listed, so an unsigned value can only narrow access, not create
    it.
    """
    ctx = await _authenticate(authorization)

    # Every ClickPost internal account is is_staff, so this claim identifies the
    # whole company rather than a team. Opening customer spend to all of it is a
    # policy call, made deliberately here rather than by whoever ships the
    # signed claim upstream.
    if ctx.is_staff and settings.budget_dashboard_open_to_all_staff:
        return ctx

    internal_user_id = _caller_internal_id(ctx, x_internal_user_id)
    if await AgentAdminRepository(session).is_admin(ctx.user_id, internal_user_id):
        # Carry the resolved id forward: routers need the person, not the shared
        # account, for the audit trail.
        return replace(ctx, internal_user_id=internal_user_id)

    logger.warning(
        "Dashboard read refused user_id=%s internal_user_id=%r enterprise_id=%s",
        ctx.user_id, internal_user_id, ctx.enterprise_id,
    )
    raise AgentAuthError(
        "You do not have access to ClickHelp admin data.", status_code=403
    )


async def require_clickpost_admin(
    authorization: str | None = Header(default=None),
    x_internal_user_id: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Write access: change caps, flip enforcement, manage the allowlist.

    Always requires an active `manager` row. A signed `is_staff` claim is not
    sufficient - being ClickPost staff should let you look, not let you change
    what every customer is allowed to spend.
    """
    ctx = await _authenticate(authorization)
    internal_user_id = _caller_internal_id(ctx, x_internal_user_id)

    grant = await AgentAdminRepository(session).find_grant(ctx.user_id, internal_user_id)
    if grant is not None and grant.can_manage:
        return replace(ctx, internal_user_id=internal_user_id)

    logger.warning(
        "Dashboard write refused user_id=%s internal_user_id=%r role=%r",
        ctx.user_id, internal_user_id, grant.role if grant else None,
    )
    raise AgentAuthError(
        "Manager access is required to change budgets or admin access. "
        "You may still view the dashboard."
        if grant is not None
        else "You do not have access to ClickHelp admin data.",
        status_code=403,
    )
