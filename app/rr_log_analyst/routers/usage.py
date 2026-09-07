"""Customer-facing usage endpoints.

What a user or an enterprise admin sees about their own consumption, as opposed
to `budget_admin.py`, which answers the same questions across every enterprise
for ClickPost staff. The split matters: nothing here takes an enterprise_id from
the caller, so there is no parameter to tamper with. Scope comes from the signed
token via `resolve_identity`, exactly as the chat endpoints do.

Both endpoints are reads. Neither creates a budget row: a period with no budget
row is a period nobody has spent in, and reporting should not be what brings one
into existence.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies.auth import (
    AgentAuthError,
    AuthContext,
    resolve_identity,
    resolve_identity_context,
)
from app.rr_log_analyst.auth import resolve_internal_user_id, resolve_is_internal_user
from app.rr_log_analyst.repositories import UserTokenRepository
from app.rr_log_analyst.repositories.budget_repo import BudgetRepository, period_bounds
from app.rr_log_analyst.repositories.usage_repo import UsageRepository, UsageTotals
from app.rr_log_analyst.models.enterprise_budget import EnterpriseBudget
from app.rr_log_analyst.schemas.usage import (
    ConversationCost,
    ConversationDetailResponse,
    ConversationsResponse,
    ConversationTurn,
    EnterpriseMember,
    EnterpriseUsageResponse,
    EnterpriseUsersResponse,
    MyUsageResponse,
    PoolState,
    SeriesPoint,
    SeriesResponse,
    TokenBreakdown,
    TokenWindow,
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_identity_query(
    user_id: int,
    enterprise_id: int,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str]:
    return await resolve_identity(
        user_id=user_id,
        enterprise_id=enterprise_id,
        authorization=authorization,
    )


async def _resolve_identity_query_ctx(
    user_id: int,
    enterprise_id: int,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str, AuthContext | None]:
    """Identity plus claims, with no admin gate.

    For reads that every seat is allowed to make but which still want a claim —
    `seat_count` on the shared pool, for instance. Distinct from
    `_resolve_admin_identity`, which refuses a non-admin outright.
    """
    return await resolve_identity_context(
        user_id=user_id,
        enterprise_id=enterprise_id,
        authorization=authorization,
    )


def _tokens(totals: UsageTotals) -> TokenBreakdown:
    return TokenBreakdown(
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        cache_read_tokens=totals.cache_read_tokens,
        cache_creation_tokens=totals.cache_creation_tokens,
        total_tokens=totals.total_tokens,
    )


def _pool_state(budget: EnterpriseBudget) -> PoolState:
    remaining = max(
        0, budget.limit_micros - budget.spent_micros - budget.reserved_micros
    )
    return PoolState(
        period_start=budget.period_start,
        period_end=budget.period_end,
        limit_micros=budget.limit_micros,
        spent_micros=budget.spent_micros,
        reserved_micros=budget.reserved_micros,
        remaining_micros=remaining,
        # Guarded because a limit of 0 is reachable through config, and a
        # ZeroDivisionError here would take down a read-only reporting call.
        consumed_ratio=(
            budget.spent_micros / budget.limit_micros if budget.limit_micros else 0.0
        ),
        enforcement_enabled=budget.enforcement_enabled,
    )


async def _pool_for(session, *, enterprise_id: int, period_start, period_end) -> PoolState:
    """The pool as both endpoints must report it.

    Shared so `/usage/me` and `/usage/enterprise` cannot disagree: a period with
    no budget row still has a budget, and showing "no pool" on one screen while
    the other shows $25 remaining, for the same enterprise, reads as a bug in
    whichever screen the user looked at second.
    """
    budget = await BudgetRepository(session).get(
        enterprise_id=enterprise_id, period_start=period_start
    )
    if budget is not None:
        return _pool_state(budget)
    return PoolState(
        period_start=period_start,
        period_end=period_end,
        limit_micros=settings.budget_default_limit_micros,
        spent_micros=0,
        reserved_micros=0,
        remaining_micros=settings.budget_default_limit_micros,
        consumed_ratio=0.0,
        enforcement_enabled=settings.budget_enforcement_default,
    )


def _resolve_period(period_start: date | None) -> tuple[date, date]:
    """Bounds for the requested billing period, defaulting to the current one.

    Any date inside the month is accepted and snapped to that month, so a client
    may send `2026-08-14` or `2026-08-01` and mean the same thing. Delegated to
    `period_bounds` rather than reimplemented, so the December rollover and the
    half-open end have exactly one definition.
    """
    if period_start is None:
        return period_bounds()
    return period_bounds(
        datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    )


def _resolve_window(
    period_start: date | None, frm: date | None, to: date | None
) -> tuple[date, date, date]:
    """Resolve the two different time questions these endpoints answer.

    Returns `(window_start, window_end, pool_period_start)`. The window is what
    usage is summed over and may be any range; the pool is a *billing period*
    and can only ever be a calendar month, because that is what an
    `enterprise_budget` row is. Conflating them would let a caller ask for "the
    pool between 15 August and 20 September", which does not exist.

    Precedence: an explicit `from`/`to` wins; otherwise `period_start` is
    shorthand for the calendar month containing it; otherwise the current
    period. When a range is given, the pool reported alongside is the period
    the range *starts* in.
    """
    if frm is not None or to is not None:
        if frm is None or to is None:
            raise HTTPException(
                status_code=422, detail="Provide both `from` and `to`, or neither."
            )
        if to <= frm:
            raise HTTPException(status_code=422, detail="`to` must be after `from`.")
        pool_start, _ = period_bounds(
            datetime.combine(frm, time.min, tzinfo=timezone.utc)
        )
        return frm, to, pool_start

    start, end = _resolve_period(period_start)
    return start, end, start


def _blank_to_none(value: str | None) -> str | None:
    """Treat an empty query parameter as absent.

    `?internal_user_id=` is what every HTTP client sends for a blank optional
    field — Postman, axios and a plain <form> all do it — and rejecting it with
    a 400 makes the obvious request fail for a reason the caller cannot see.
    Absent and empty mean the same thing on a GET.
    """
    return value if (value or "").strip() else None


def _token_window(row) -> TokenWindow | None:
    """Mirror `UserTokenRepository._effective_limit` and `can_start`.

    Kept in step with those deliberately: if this drifts, the UI tells a user
    they have tokens left while the next request is refused, which reads as the
    agent being broken rather than as a limit doing its job.
    """
    if row is None:
        return None

    limit_tokens = (
        row.max_input_tokens
        + row.max_output_tokens
        + (row.extra_token if row.extra_token_used else 0)
    )
    used_tokens = row.used_input_tokens + row.used_output_tokens
    now = datetime.now(timezone.utc)

    return TokenWindow(
        used_tokens=used_tokens,
        limit_tokens=limit_tokens,
        remaining_tokens=max(0, limit_tokens - used_tokens),
        used_input_tokens=row.used_input_tokens,
        used_output_tokens=row.used_output_tokens,
        max_input_tokens=row.max_input_tokens,
        max_output_tokens=row.max_output_tokens,
        extra_tokens=row.extra_token,
        extra_tokens_claimed=row.extra_token_used,
        window_started_at=row.window_start,
        resets_at=row.reset_at,
        # A window past its reset is not exhausted even with the counters full:
        # the next request rolls it over before checking. Reporting it as
        # exhausted would tell a user to wait when they can send right now.
        exhausted=now < row.reset_at and used_tokens >= limit_tokens,
    )


@router.get("/me", response_model=MyUsageResponse)
async def my_usage(
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    internal_user_id: str | None = None,
    is_internal_user: bool = False,
    period_start: date | None = Query(
        None,
        description=(
            "Any date inside the billing period to report on; snapped to that "
            "month. Shorthand for a from/to spanning that month."
        ),
    ),
    frm: date | None = Query(
        None,
        alias="from",
        description="Inclusive UTC start of an arbitrary window. Overrides period_start.",
    ),
    to: date | None = Query(
        None, description="Exclusive UTC end. Required whenever `from` is given."
    ),
    session: AsyncSession = Depends(get_db),
):
    """This caller's own spend, and the state of both limits that can stop them.

    Two limits apply independently and a user can be blocked by either: the
    24-hour token window, which clears on its own, and the enterprise spend
    pool, which does not. They need different messages, so both are reported
    rather than collapsed into one "blocked" flag.
    """
    user_id, enterprise_id, _ = identity
    is_internal_user = resolve_is_internal_user(is_internal_user)
    internal_user_id = resolve_internal_user_id(_blank_to_none(internal_user_id))
    if not is_internal_user:
        internal_user_id = None

    window_start, window_end, pool_period = _resolve_window(period_start, frm, to)
    usage_repo = UsageRepository(session)

    totals = await usage_repo.for_user(
        enterprise_id=enterprise_id,
        user_id=user_id,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
        period_start=window_start,
        period_end=window_end,
    )

    token_row = await UserTokenRepository(session).get_owned(
        user_id=user_id,
        enterprise_id=enterprise_id,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
    )

    # Internal ClickPost activity never draws on the customer's pool, so showing
    # them one here would imply their turns are consuming it.
    # Internal ClickPost activity never draws on the customer's pool, so showing
    # one here would imply these turns are consuming it.
    pool = None
    if not is_internal_user:
        pool_start, pool_end = _resolve_period(pool_period)
        pool = await _pool_for(
            session,
            enterprise_id=enterprise_id,
            period_start=pool_start,
            period_end=pool_end,
        )

    return MyUsageResponse(
        user_id=user_id,
        enterprise_id=enterprise_id,
        is_internal_user=is_internal_user,
        period_start=window_start,
        period_end=window_end,
        turns=totals.turns,
        cost_micros=totals.cost_micros,
        tokens=_tokens(totals),
        last_active=totals.last_active,
        # Per-user allocation is not built yet (track E). Null is the honest
        # answer and means "the whole remaining pool is available to you";
        # a number here would have to be invented.
        allocation_micros=None,
        token_window=_token_window(token_row),
        pool=pool,
    )


@router.get("/enterprise", response_model=EnterpriseUsageResponse)
async def enterprise_usage(
    identity: tuple[int, int, str, AuthContext | None] = Depends(
        _resolve_identity_query_ctx
    ),
    period_start: date | None = Query(
        None,
        description=(
            "Any date inside the billing period to report on; snapped to that "
            "month. Shorthand for a from/to spanning that month."
        ),
    ),
    frm: date | None = Query(
        None,
        alias="from",
        description="Inclusive UTC start of an arbitrary window. Overrides period_start.",
    ),
    to: date | None = Query(
        None, description="Exclusive UTC end. Required whenever `from` is given."
    ),
    session: AsyncSession = Depends(get_db),
):
    """The enterprise's shared pool and what it has spent this period.

    Every seat may read this: the pool is shared, and someone refused for
    `budget_enterprise` needs to see that the org is out of budget rather than
    that something is broken. Per-user detail is a different endpoint and is
    restricted to an enterprise admin.
    """
    _, enterprise_id, _, ctx = identity
    window_start, window_end, pool_period = _resolve_window(period_start, frm, to)
    pool_start, pool_end = _resolve_period(pool_period)

    usage_repo = UsageRepository(session)

    totals = await usage_repo.for_enterprise(
        enterprise_id=enterprise_id,
        period_start=window_start,
        period_end=window_end,
    )
    active = await usage_repo.active_users(
        enterprise_id=enterprise_id,
        period_start=window_start,
        period_end=window_end,
    )

    pool = await _pool_for(
        session,
        enterprise_id=enterprise_id,
        period_start=pool_start,
        period_end=pool_end,
    )

    return EnterpriseUsageResponse(
        enterprise_id=enterprise_id,
        pool=pool,
        turns=totals.turns,
        cost_micros=totals.cost_micros,
        tokens=_tokens(totals),
        active_users=active,
        # From clickpost's signed claim. Counting our own tables instead would
        # miss every seat that has not yet sent a message, which is exactly the
        # population a per-user split must not forget.
        seat_count=ctx.seat_count if ctx else None,
        cost_per_turn_micros=(
            totals.cost_micros // totals.turns if totals.turns else 0
        ),
    )


async def _resolve_admin_identity(
    user_id: int,
    enterprise_id: int,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str, AuthContext | None]:
    """Identity for the endpoints that expose other people's spend.

    `is_enterprise_admin` is not tri-state: an unsigned token is refused rather
    than falling back to anything. This grants sight of every colleague's
    activity, and the failure mode of guessing generously is one seat reading
    the whole company's usage.

    The exception is JWT auth being switched off entirely, which only happens in
    local development — there is no token to carry a claim, and the identity is
    already coming from query parameters, so there is nothing left to enforce.
    """
    uid, eid, token, ctx = await resolve_identity_context(
        user_id=user_id,
        enterprise_id=enterprise_id,
        authorization=authorization,
    )
    if ctx is not None and not ctx.is_enterprise_admin:
        raise AgentAuthError(
            "This view is for enterprise administrators. Your account can see its "
            "own usage at /usage/me.",
            status_code=403,
        )
    return uid, eid, token, ctx


@router.get("/enterprise/users", response_model=EnterpriseUsersResponse)
async def enterprise_users(
    identity: tuple[int, int, str, AuthContext | None] = Depends(_resolve_admin_identity),
    period_start: date | None = Query(
        None,
        description=(
            "Any date inside the billing period to report on; snapped to that "
            "month. Shorthand for a from/to spanning that month."
        ),
    ),
    frm: date | None = Query(
        None,
        alias="from",
        description="Inclusive UTC start of an arbitrary window. Overrides period_start.",
    ),
    to: date | None = Query(
        None, description="Exclusive UTC end. Required whenever `from` is given."
    ),
    session: AsyncSession = Depends(get_db),
):
    """Per-member spend for the period, ranked, highest first.

    ClickPost support activity is excluded rather than flagged: it never touched
    this enterprise's pool, and listing a stranger among "your team" raises a
    question whose answer is "ignore that row".
    """
    _, enterprise_id, _, ctx = identity
    window_start, window_end, _pool = _resolve_window(period_start, frm, to)

    members = await UsageRepository(session).users(
        enterprise_id=enterprise_id,
        period_start=window_start,
        period_end=window_end,
    )
    total = sum(m["cost_micros"] for m in members)

    return EnterpriseUsersResponse(
        enterprise_id=enterprise_id,
        period_start=window_start,
        period_end=window_end,
        members=[
            EnterpriseMember(
                **m,
                # Share of what was actually spent, not of the cap: with the
                # pool half used, "50%" against the cap would read as this one
                # person being halfway through the company's budget.
                share_of_spend=(m["cost_micros"] / total) if total else 0.0,
                allocation_micros=None,
            )
            for m in members
        ],
        seat_count=ctx.seat_count if ctx else None,
        unallocated_micros=None,
    )


@router.get("/enterprise/series", response_model=SeriesResponse)
async def enterprise_series(
    identity: tuple[int, int, str, AuthContext | None] = Depends(_resolve_admin_identity),
    granularity: str = "day",
    days: int = 30,
    frm: date | None = Query(
        None,
        alias="from",
        description="Inclusive UTC start. Overrides `days` when given with `to`.",
    ),
    to: date | None = Query(None, description="Exclusive UTC end."),
    session: AsyncSession = Depends(get_db),
):
    """Cost and volume over time at day, week or month granularity.

    Volume travels with cost deliberately: a bill that doubled because usage
    doubled and one that doubled because a prompt got more expensive look
    identical in a spend line on its own, and call for opposite responses.
    """
    _, enterprise_id, _, _ = identity
    if granularity not in ("day", "week", "month"):
        raise HTTPException(
            status_code=422,
            detail="granularity must be one of: day, week, month",
        )
    # An explicit range wins over `days`, so a month picker and a rolling window
    # can share one endpoint. `days` alone stays the default for a dashboard that
    # just wants "recent".
    if frm is not None or to is not None:
        if frm is None or to is None:
            raise HTTPException(
                status_code=422, detail="Provide both `from` and `to`, or neither."
            )
        if to <= frm:
            raise HTTPException(status_code=422, detail="`to` must be after `from`.")
        window_from = datetime.combine(frm, time.min, tzinfo=timezone.utc)
        window_to = datetime.combine(to, time.min, tzinfo=timezone.utc)
    else:
        if not 1 <= days <= 730:
            raise HTTPException(status_code=422, detail="days must be between 1 and 730")
        window_to = datetime.now(timezone.utc)
        window_from = window_to - timedelta(days=days)

    points = await UsageRepository(session).series(
        enterprise_id=enterprise_id,
        frm=window_from,
        to=window_to,
        granularity=granularity,
    )
    return SeriesResponse(
        enterprise_id=enterprise_id,
        granularity=granularity,
        **{"from": window_from},
        to=window_to,
        points=[SeriesPoint(**p) for p in points],
    )


@router.get("/enterprise/conversations", response_model=ConversationsResponse)
async def enterprise_conversations(
    identity: tuple[int, int, str, AuthContext | None] = Depends(_resolve_admin_identity),
    limit: int = 20,
    period_start: date | None = Query(
        None,
        description=(
            "Any date inside the billing period to report on; snapped to that "
            "month. Shorthand for a from/to spanning that month."
        ),
    ),
    frm: date | None = Query(
        None,
        alias="from",
        description="Inclusive UTC start of an arbitrary window. Overrides period_start.",
    ),
    to: date | None = Query(
        None, description="Exclusive UTC end. Required whenever `from` is given."
    ),
    session: AsyncSession = Depends(get_db),
):
    """Conversations ranked by cost — the answer to "what did we spend it on"."""
    _, enterprise_id, _, _ = identity
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")

    window_start, window_end, _pool = _resolve_window(period_start, frm, to)
    rows = await UsageRepository(session).conversations(
        enterprise_id=enterprise_id,
        period_start=window_start,
        period_end=window_end,
        limit=limit,
    )
    return ConversationsResponse(
        enterprise_id=enterprise_id,
        period_start=window_start,
        period_end=window_end,
        conversations=[ConversationCost(**r) for r in rows],
    )


@router.get(
    "/enterprise/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
)
async def enterprise_conversation_detail(
    conversation_id: uuid.UUID,
    identity: tuple[int, int, str, AuthContext | None] = Depends(_resolve_admin_identity),
    session: AsyncSession = Depends(get_db),
):
    """Turn-by-turn cost inside one conversation.

    404 rather than an empty list for an unknown id: an empty turn list would be
    indistinguishable from a conversation that exists and cost nothing.
    """
    _, enterprise_id, _, _ = identity
    turns = await UsageRepository(session).conversation_turns(
        enterprise_id=enterprise_id, conversation_id=conversation_id
    )
    if not turns:
        raise HTTPException(
            status_code=404,
            detail="No settled turns for that conversation in this enterprise.",
        )
    return ConversationDetailResponse(
        enterprise_id=enterprise_id,
        conversation_id=conversation_id,
        turns=[ConversationTurn(**t) for t in turns],
        cost_micros=sum(t["cost_micros"] for t in turns),
    )


@router.get("/enterprise/export.csv")
async def enterprise_export(
    identity: tuple[int, int, str, AuthContext | None] = Depends(_resolve_admin_identity),
    period_start: date | None = Query(
        None,
        description=(
            "Any date inside the billing period to report on; snapped to that "
            "month. Shorthand for a from/to spanning that month."
        ),
    ),
    frm: date | None = Query(
        None,
        alias="from",
        description="Inclusive UTC start of an arbitrary window. Overrides period_start.",
    ),
    to: date | None = Query(
        None, description="Exclusive UTC end. Required whenever `from` is given."
    ),
    session: AsyncSession = Depends(get_db),
):
    """Every ledger row for the period as CSV.

    Streamed, not assembled: a busy month is tens of thousands of rows, and
    building the whole file in memory to hand it straight back is how an export
    becomes the thing that takes the service down.

    Money is written as integer micros *and* as dollars — the integer is what
    reconciles, the dollar column is what opens correctly in a spreadsheet.
    """
    _, enterprise_id, _, _ = identity
    window_start, window_end, _pool = _resolve_window(period_start, frm, to)
    repo = UsageRepository(session)

    async def rows():
        header = (
            "created_at,user_id,is_internal_user,counted_against_pool,conversation_id,"
            "model,input_tokens,cache_read_tokens,cache_creation_tokens,output_tokens,"
            "secondary_model,secondary_input_tokens,secondary_output_tokens,"
            "cost_micros,cost_usd,pricing_failed\n"
        )
        yield header
        async for r in repo.export_rows(
            enterprise_id=enterprise_id,
            period_start=window_start,
            period_end=window_end,
        ):
            yield (
                f"{r.created_at.isoformat()},{r.user_id},{r.is_internal_user},"
                f"{r.counted_against_pool},{r.conversation_id},{r.model},"
                f"{r.input_tokens},{r.cache_read_tokens},{r.cache_creation_tokens},"
                f"{r.output_tokens},{r.secondary_model or ''},"
                f"{r.secondary_input_tokens},{r.secondary_output_tokens},"
                f"{r.cost_micros},{r.cost_micros / 1_000_000:.6f},{r.pricing_failed}\n"
            )

    # Name it after the actual window, so two exports from different ranges
    # cannot land in a downloads folder under the same name.
    filename = (
        f"clickhelp-usage-{enterprise_id}-{window_start:%Y-%m-%d}"
        f"_{window_end:%Y-%m-%d}.csv"
    )
    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
