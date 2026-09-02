"""Internal budget dashboard API.

Cross-enterprise, read-only except for one operational write. Gated by
`require_clickpost_admin` on every route.

Money is returned as integer micro-dollars throughout, with a `_usd` float
alongside for display. The integer is authoritative; the float exists so the
client never has to divide, and must never be summed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.dependencies.admin import require_clickpost_admin, require_clickpost_staff
from app.dependencies.auth import AuthContext
from app.rr_log_analyst.repositories.budget_analytics_repo import (
    BudgetAnalyticsRepository,
    default_range,
)
from app.rr_log_analyst.models.admin_audit_log import AdminAuditLog
from app.rr_log_analyst.repositories.audit_repo import AuditRepository
from app.rr_log_analyst.repositories.budget_repo import period_bounds
from app.rr_log_analyst.schemas.budget_admin import (
    BlocksResponse,
    BreakdownResponse,
    EnterpriseDetailResponse,
    EnterpriseConfigRequest,
    OverviewResponse,
    PressureResponse,
    TrendResponse,
)

logger = logging.getLogger(__name__)

# Reads are open to any ClickPost staff member; the single write below raises
# the bar to manager.
router = APIRouter(dependencies=[Depends(require_clickpost_staff)])


def _window(frm: date | None, to: date | None) -> tuple[datetime, datetime]:
    """Resolve the date filter every read endpoint shares.

    `to` is treated as inclusive of the whole day, because a caller asking for
    "1 Aug to 20 Aug" means through the end of the 20th, not up to midnight
    at its start.
    """
    if frm is None or to is None:
        return default_range()
    start = datetime.combine(frm, time.min, tzinfo=timezone.utc)
    end = datetime.combine(to, time.max, tzinfo=timezone.utc)
    if end <= start:
        raise HTTPException(status_code=422, detail="`to` must be after `from`.")
    return start, end


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    frm: date | None = Query(None, alias="from"),
    to: date | None = None,
    include_internal: bool = False,
    session: AsyncSession = Depends(get_db),
):
    """Headline figures: spend, capacity, cost per turn, cache hit rate."""
    start, end = _window(frm, to)
    data = await BudgetAnalyticsRepository(session).overview(
        frm=start, to=end, include_internal=include_internal
    )
    return OverviewResponse(**data)


@router.get("/pressure", response_model=PressureResponse)
async def pressure(
    limit: int = Query(50, ge=1, le=200),
    period_start: date | None = Query(
        None,
        description="First day of the period, e.g. 2026-08-01. Defaults to the current month.",
    ),
    session: AsyncSession = Depends(get_db),
):
    """Enterprises ranked by share of cap consumed, with a run-rate projection.

    `projected_over` is the actionable field — it names who will breach before
    they do, rather than reporting it afterwards.

    Defaults to the current month, which on the 1st legitimately shows everyone
    near zero. Pass `period_start` to look at a month that has actually run.
    """
    resolved = period_start or period_bounds()[0]
    rows = await BudgetAnalyticsRepository(session).pressure(
        limit=limit, period_start=resolved
    )
    return PressureResponse(period_start=resolved, enterprises=rows)


@router.get("/breakdown", response_model=BreakdownResponse)
async def breakdown(
    frm: date | None = Query(None, alias="from"),
    to: date | None = None,
    include_internal: bool = False,
    session: AsyncSession = Depends(get_db),
):
    """Where the money goes, by token class and by model."""
    start, end = _window(frm, to)
    return BreakdownResponse(
        **await BudgetAnalyticsRepository(session).breakdown(
            frm=start, to=end, include_internal=include_internal
        )
    )


@router.get("/trend", response_model=TrendResponse)
async def trend(
    frm: date | None = Query(None, alias="from"),
    to: date | None = None,
    include_internal: bool = False,
    enterprise_id: int | None = None,
    session: AsyncSession = Depends(get_db),
):
    """Cost per turn per day, with volume, so price and usage stay separable."""
    start, end = _window(frm, to)
    days = await BudgetAnalyticsRepository(session).trend(
        frm=start, to=end, include_internal=include_internal, enterprise_id=enterprise_id
    )
    return TrendResponse(**{"from": start, "to": end, "days": days})


@router.get("/blocks", response_model=BlocksResponse)
async def blocks(
    frm: date | None = Query(None, alias="from"),
    to: date | None = None,
    enterprise_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    """Every request the limit layer turned away, and which limit did it."""
    start, end = _window(frm, to)
    events = await BudgetAnalyticsRepository(session).blocks(
        frm=start, to=end, enterprise_id=enterprise_id, limit=limit
    )
    return BlocksResponse(**{"from": start, "to": end, "events": events})


@router.get("/enterprises/{enterprise_id}", response_model=EnterpriseDetailResponse)
async def enterprise_detail(
    enterprise_id: int,
    frm: date | None = Query(None, alias="from"),
    to: date | None = None,
    session: AsyncSession = Depends(get_db),
):
    """One enterprise: budget state, spend per user, daily series, top conversations."""
    start, end = _window(frm, to)
    repo = BudgetAnalyticsRepository(session)
    period_start = period_bounds()[0]

    matches = [
        e
        for e in await repo.pressure(limit=500, period_start=period_start)
        if e["enterprise_id"] == enterprise_id
    ]
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"No budget row for enterprise {enterprise_id} in the current period.",
        )

    return EnterpriseDetailResponse(
        **{
            "from": start,
            "to": end,
            "budget": matches[0],
            "users": await repo.enterprise_users(
                enterprise_id=enterprise_id, frm=start, to=end
            ),
            "days": await repo.trend(
                frm=start, to=end, include_internal=True, enterprise_id=enterprise_id
            ),
            "top_conversations": await repo.top_conversations(
                frm=start, to=end, enterprise_id=enterprise_id
            ),
        }
    )


@router.get("/audit")
async def audit_trail(
    frm: date | None = Query(None, alias="from"),
    to: date | None = None,
    target_type: str | None = Query(None, description="enterprise | admin"),
    target_id: str | None = Query(None, description="enterprise id, or the granted user id"),
    actor_internal_user_id: str | None = Query(None, description="filter by who did it"),
    action: str | None = Query(None, description="e.g. budget.limit_changed"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    """Who changed what, newest first.

    Readable by any viewer: knowing that a colleague raised a cap is not
    sensitive, and making it visible is most of the point.
    """
    start, end = _window(frm, to)
    rows = await AuditRepository(session).list(
        frm=start, to=end, target_type=target_type, target_id=target_id,
        actor_internal_user_id=actor_internal_user_id, action=action, limit=limit,
    )
    return {
        "from": start,
        "to": end,
        "entries": [
            {
                "created_at": r.created_at,
                "actor_user_id": r.actor_user_id,
                "actor_internal_user_id": r.actor_internal_user_id,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "before": r.before,
                "after": r.after,
            }
            for r in rows
        ],
    }


@router.patch("/enterprises/{enterprise_id}")
async def set_enterprise_config(
    enterprise_id: int,
    body: EnterpriseConfigRequest,
    caller: AuthContext = Depends(require_clickpost_admin),
    session: AsyncSession = Depends(get_db),
):
    """Change an enterprise's cap, or switch enforcement on and off.

    The only write on this surface, and the escape hatch when a cap turns out
    wrong: one call, no deploy.
    """
    if body.limit_micros is None and body.enforcement_enabled is None:
        raise HTTPException(
            status_code=422,
            detail="Provide limit_micros, enforcement_enabled, or both.",
        )

    # A cap below one turn's hold can never admit a request, so the enterprise
    # would be hard-blocked while its dashboard still showed money remaining.
    # Observed in testing with a $0.05 cap against a $0.50 hold.
    estimate = settings.budget_reservation_estimate_micros
    if body.limit_micros is not None and body.limit_micros < estimate:
        raise HTTPException(
            status_code=422,
            detail=(
                f"limit_micros must be at least the reservation estimate "
                f"({estimate} micros). A lower cap can never admit a request, "
                f"so the enterprise would be blocked with budget still remaining."
            ),
        )

    period_start = period_bounds()[0]
    repo = BudgetAnalyticsRepository(session)

    # Snapshot before the update: an audit entry saying a cap "became $30" is
    # only half the story without what it was.
    existing = [
        e
        for e in await repo.pressure(limit=1000, period_start=period_start)
        if e["enterprise_id"] == enterprise_id
    ]
    before = (
        {
            "limit_micros": existing[0]["limit_micros"],
            "enforcement_enabled": existing[0]["enforcement_enabled"],
        }
        if existing
        else None
    )

    row = await repo.set_enterprise_config(
        enterprise_id=enterprise_id,
        period_start=period_start,
        limit_micros=body.limit_micros,
        enforcement_enabled=body.enforcement_enabled,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No budget row for enterprise {enterprise_id} in the current period. "
                f"It is created on their first turn of the month."
            ),
        )
    # Same transaction as the change, so a write cannot land without its trail.
    await AuditRepository(session).record(
        actor_user_id=caller.user_id,
        actor_internal_user_id=caller.internal_user_id,
        action=(
            AdminAuditLog.BUDGET_LIMIT_CHANGED
            if body.limit_micros is not None
            else AdminAuditLog.BUDGET_ENFORCEMENT_CHANGED
        ),
        target_type=AdminAuditLog.TARGET_ENTERPRISE,
        target_id=enterprise_id,
        before=before,
        after={
            "limit_micros": row.limit_micros,
            "enforcement_enabled": row.enforcement_enabled,
        },
    )
    await session.commit()

    logger.info(
        "Budget config changed enterprise_id=%s by=%s/%s limit_micros=%s enforcement=%s",
        enterprise_id,
        caller.user_id,
        caller.internal_user_id,
        body.limit_micros,
        body.enforcement_enabled,
    )
    return {
        "enterprise_id": row.enterprise_id,
        "period_start": row.period_start,
        "limit_micros": row.limit_micros,
        "limit_usd": round(row.limit_micros / 1_000_000, 2),
        "spent_micros": row.spent_micros,
        "enforcement_enabled": row.enforcement_enabled,
        "changed_by": caller.user_id,
    }
