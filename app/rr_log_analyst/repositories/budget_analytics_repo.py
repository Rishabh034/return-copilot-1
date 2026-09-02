"""Read queries behind the internal budget dashboard.

Everything here reads `llm_usage_ledger` and `enterprise_budget`. Nothing writes
except `set_enterprise_config`, which is the one operational escape hatch for
changing a cap or flipping enforcement.

Two conventions the callers depend on:

Cost is stored per turn, not per token class, so a breakdown by class has to be
recomputed from the token counts. That happens in Python against
`services.pricing` rather than by embedding rates in SQL, so there stays exactly
one place where a price lives.

`counted_against_pool` separates customer spend from ClickPost support activity.
Reporting that mixes them overstates what a customer costs; reporting that drops
support entirely loses what our own team costs. Both are returned, separately.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Float, and_, case, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.rr_log_analyst.models.budget_block_event import BudgetBlockEvent
from app.rr_log_analyst.models.enterprise_budget import EnterpriseBudget
from app.rr_log_analyst.models.usage_ledger import LlmUsageLedger
from app.rr_log_analyst.repositories.budget_repo import period_bounds
from app.rr_log_analyst.services.pricing import MODEL_RATES, rates_for

# An enterprise is "at risk" once it has consumed this share of its cap.
AT_RISK_THRESHOLD = 0.8


def default_range() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=30), now


class BudgetAnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _scope(self, frm: datetime, to: datetime, include_internal: bool):
        clauses = [LlmUsageLedger.created_at >= frm, LlmUsageLedger.created_at < to]
        if not include_internal:
            clauses.append(LlmUsageLedger.counted_against_pool.is_(True))
        return and_(*clauses)

    # --- C4 -----------------------------------------------------------------

    async def overview(
        self, *, frm: datetime, to: datetime, include_internal: bool = False
    ) -> dict:
        row = (
            await self._session.execute(
                select(
                    func.count().label("turns"),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0).label("cost"),
                    func.coalesce(func.sum(LlmUsageLedger.input_tokens), 0).label("inp"),
                    func.coalesce(func.sum(LlmUsageLedger.output_tokens), 0).label("out"),
                    func.coalesce(
                        func.sum(LlmUsageLedger.cache_read_tokens), 0
                    ).label("cache_read"),
                    func.count(func.distinct(LlmUsageLedger.enterprise_id)).label("ents"),
                    func.count(func.distinct(LlmUsageLedger.user_id)).label("users"),
                    func.coalesce(
                        func.sum(
                            case((LlmUsageLedger.pricing_failed.is_(True), 1), else_=0)
                        ),
                        0,
                    ).label("unpriced"),
                ).where(self._scope(frm, to, include_internal))
            )
        ).one()

        support = (
            await self._session.execute(
                select(func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0)).where(
                    LlmUsageLedger.created_at >= frm,
                    LlmUsageLedger.created_at < to,
                    LlmUsageLedger.counted_against_pool.is_(False),
                )
            )
        ).scalar_one()

        period_start, _ = period_bounds()
        pool = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(EnterpriseBudget.limit_micros), 0),
                    func.coalesce(func.sum(EnterpriseBudget.reserved_micros), 0),
                    func.count(),
                    func.coalesce(
                        func.sum(
                            case(
                                (EnterpriseBudget.enforcement_enabled.is_(True), 1),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    EnterpriseBudget.spent_micros
                                    >= AT_RISK_THRESHOLD * EnterpriseBudget.limit_micros,
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                ).where(EnterpriseBudget.period_start == period_start)
            )
        ).one()

        turns = row.turns or 0
        return {
            "from": frm,
            "to": to,
            "turns": turns,
            "cost_micros": int(row.cost),
            "support_cost_micros": int(support),
            "cost_per_turn_micros": int(row.cost // turns) if turns else 0,
            "input_tokens": int(row.inp),
            "output_tokens": int(row.out),
            "cache_read_tokens": int(row.cache_read),
            # The lever that matters most: a low hit rate means every turn is
            # paying full price for a prefix it could have reused.
            "cache_hit_rate": (row.cache_read / row.inp) if row.inp else 0.0,
            "active_enterprises": int(row.ents),
            "active_users": int(row.users),
            "unpriced_turns": int(row.unpriced),
            "period_start": period_start,
            "total_capacity_micros": int(pool[0]),
            "held_now_micros": int(pool[1]),
            "enterprises_with_budget": int(pool[2]),
            "enterprises_enforced": int(pool[3]),
            "enterprises_at_risk": int(pool[4]),
        }

    # --- C5 -----------------------------------------------------------------

    async def pressure(self, *, limit: int = 50, period_start: date | None = None) -> list[dict]:
        """Enterprises ranked by share of cap consumed, with a run-rate projection.

        The projection is the column that earns this screen its place: it names
        who *will* breach before they do, from elapsed-day spend rate, rather
        than reporting a breach after the fact.
        """
        period_start = period_start or period_bounds()[0]
        rows = (
            await self._session.execute(
                select(EnterpriseBudget)
                .where(EnterpriseBudget.period_start == period_start)
                .order_by(
                    # Float, not Integer: Postgres integer division truncates, so
                    # an Integer cast makes every enterprise under its cap sort as
                    # 0 and the ranking becomes arbitrary.
                    (
                        cast(EnterpriseBudget.spent_micros, Float)
                        / func.nullif(EnterpriseBudget.limit_micros, 0)
                    ).desc()
                )
                .limit(limit)
            )
        ).scalars().all()

        today = datetime.now(timezone.utc).date()
        out = []
        for r in rows:
            total_days = (r.period_end - r.period_start).days or 1
            elapsed = max(1, min((today - r.period_start).days + 1, total_days))
            projected = int(r.spent_micros / elapsed * total_days)
            out.append(
                {
                    "enterprise_id": r.enterprise_id,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "limit_micros": r.limit_micros,
                    "spent_micros": r.spent_micros,
                    "reserved_micros": r.reserved_micros,
                    "remaining_micros": max(
                        0, r.limit_micros - r.spent_micros - r.reserved_micros
                    ),
                    "consumed_ratio": (r.spent_micros / r.limit_micros)
                    if r.limit_micros
                    else 0.0,
                    "projected_micros": projected,
                    "projected_over": projected > r.limit_micros,
                    "days_elapsed": elapsed,
                    "days_total": total_days,
                    "enforcement_enabled": r.enforcement_enabled,
                    "seat_count": r.seat_count,
                }
            )
        return out

    # --- C6 -----------------------------------------------------------------

    async def breakdown(
        self, *, frm: datetime, to: datetime, include_internal: bool = False
    ) -> dict:
        """Spend split by token class and by model.

        Answers "which lever is worth pulling". Output tokens routinely dominate,
        which makes response length a better target than context trimming.
        """
        rows = (
            await self._session.execute(
                select(
                    LlmUsageLedger.model,
                    func.coalesce(func.sum(LlmUsageLedger.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.cache_read_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.cache_creation_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.output_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.secondary_input_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.secondary_output_tokens), 0),
                    func.count(),
                )
                .where(self._scope(frm, to, include_internal))
                .group_by(LlmUsageLedger.model)
            )
        ).all()

        classes = {
            "uncached_input": 0,
            "cache_read": 0,
            "cache_write": 0,
            "output": 0,
            "secondary": 0,
        }
        by_model: list[dict] = []

        for model, inp, cread, cwrite, out, sin, sout, turns in rows:
            try:
                r = rates_for(model)
            except Exception:
                # An unpriced model still shows its volume; it just cannot be
                # costed until a rate is added.
                by_model.append(
                    {"model": model, "turns": turns, "cost_micros": 0, "unpriced": True}
                )
                continue
            uncached = max(0, inp - cread - cwrite)
            parts = {
                "uncached_input": uncached * r.input // 1000,
                "cache_read": cread * r.cache_read // 1000,
                "cache_write": cwrite * r.cache_write // 1000,
                "output": out * r.output // 1000,
            }
            secondary = 0
            if sin or sout:
                # The scratchpad always runs on the configured secondary model.
                sr = MODEL_RATES.get("claude-haiku-4-5")
                if sr:
                    secondary = sin * sr.input // 1000 + sout * sr.output // 1000
            for k, v in parts.items():
                classes[k] += v
            classes["secondary"] += secondary
            by_model.append(
                {
                    "model": model,
                    "turns": turns,
                    "cost_micros": sum(parts.values()) + secondary,
                    "unpriced": False,
                }
            )

        total = sum(classes.values())
        return {
            "from": frm,
            "to": to,
            "total_micros": total,
            "by_class": [
                {"cls": k, "cost_micros": v, "share": (v / total) if total else 0.0}
                for k, v in sorted(classes.items(), key=lambda kv: -kv[1])
            ],
            "by_model": sorted(by_model, key=lambda m: -m["cost_micros"]),
        }

    # --- C7 -----------------------------------------------------------------

    async def trend(
        self,
        *,
        frm: datetime,
        to: datetime,
        include_internal: bool = False,
        enterprise_id: int | None = None,
    ) -> list[dict]:
        """Cost per turn per day, with volume alongside.

        Both numbers together, because a rising bill from more usage and a
        rising bill from a more expensive prompt need opposite responses.
        """
        clauses = [self._scope(frm, to, include_internal)]
        if enterprise_id is not None:
            clauses.append(LlmUsageLedger.enterprise_id == enterprise_id)

        day = func.date(LlmUsageLedger.created_at).label("day")
        rows = (
            await self._session.execute(
                select(
                    day,
                    func.count().label("turns"),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0).label("cost"),
                    func.coalesce(func.sum(LlmUsageLedger.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.output_tokens), 0),
                    func.count(func.distinct(LlmUsageLedger.user_id)),
                )
                .where(and_(*clauses))
                .group_by(day)
                .order_by(day)
            )
        ).all()

        return [
            {
                "day": d,
                "turns": t,
                "cost_micros": int(c),
                "cost_per_turn_micros": int(c // t) if t else 0,
                "input_tokens": int(i),
                "output_tokens": int(o),
                "active_users": int(u),
            }
            for d, t, c, i, o, u in rows
        ]

    # --- C8 -----------------------------------------------------------------

    async def blocks(
        self, *, frm: datetime, to: datetime, limit: int = 100,
        enterprise_id: int | None = None,
    ) -> list[dict]:
        clauses = [
            BudgetBlockEvent.created_at >= frm,
            BudgetBlockEvent.created_at < to,
        ]
        if enterprise_id is not None:
            clauses.append(BudgetBlockEvent.enterprise_id == enterprise_id)

        rows = (
            await self._session.execute(
                select(BudgetBlockEvent)
                .where(and_(*clauses))
                .order_by(BudgetBlockEvent.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

        return [
            {
                "created_at": r.created_at,
                "enterprise_id": r.enterprise_id,
                "user_id": r.user_id,
                "is_internal_user": r.is_internal_user,
                # The field that separates "wait a few hours" from "your company
                # is out until next month".
                "limit_kind": r.limit_kind,
                "limit_micros": r.limit_micros,
                "spent_micros": r.spent_micros,
                "estimate_micros": r.estimate_micros,
            }
            for r in rows
        ]

    # --- C9 -----------------------------------------------------------------

    async def enterprise_users(
        self, *, enterprise_id: int, frm: datetime, to: datetime
    ) -> list[dict]:
        rows = (
            await self._session.execute(
                select(
                    LlmUsageLedger.user_id,
                    LlmUsageLedger.is_internal_user,
                    func.count().label("turns"),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0).label("cost"),
                    func.coalesce(func.sum(LlmUsageLedger.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsageLedger.output_tokens), 0),
                    func.max(LlmUsageLedger.created_at),
                )
                .where(
                    LlmUsageLedger.enterprise_id == enterprise_id,
                    LlmUsageLedger.created_at >= frm,
                    LlmUsageLedger.created_at < to,
                )
                .group_by(LlmUsageLedger.user_id, LlmUsageLedger.is_internal_user)
                .order_by(func.sum(LlmUsageLedger.cost_micros).desc())
            )
        ).all()

        return [
            {
                "user_id": u,
                "is_internal_user": internal,
                "turns": t,
                "cost_micros": int(c),
                "input_tokens": int(i),
                "output_tokens": int(o),
                "last_active": last,
            }
            for u, internal, t, c, i, o, last in rows
        ]

    async def top_conversations(
        self, *, frm: datetime, to: datetime, enterprise_id: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        clauses = [
            LlmUsageLedger.created_at >= frm,
            LlmUsageLedger.created_at < to,
        ]
        if enterprise_id is not None:
            clauses.append(LlmUsageLedger.enterprise_id == enterprise_id)

        rows = (
            await self._session.execute(
                select(
                    LlmUsageLedger.conversation_id,
                    LlmUsageLedger.enterprise_id,
                    func.count().label("turns"),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0).label("cost"),
                    func.max(LlmUsageLedger.created_at),
                )
                .where(and_(*clauses))
                .group_by(LlmUsageLedger.conversation_id, LlmUsageLedger.enterprise_id)
                .order_by(func.sum(LlmUsageLedger.cost_micros).desc())
                .limit(limit)
            )
        ).all()

        return [
            {
                "conversation_id": cid,
                "enterprise_id": eid,
                "turns": t,
                "cost_micros": int(c),
                "last_active": last,
            }
            for cid, eid, t, c, last in rows
        ]

    # --- C10 ----------------------------------------------------------------

    async def set_enterprise_config(
        self,
        *,
        enterprise_id: int,
        period_start: date,
        limit_micros: int | None = None,
        enforcement_enabled: bool | None = None,
    ) -> EnterpriseBudget | None:
        values: dict = {"updated_at": datetime.now(timezone.utc)}
        if limit_micros is not None:
            values["limit_micros"] = limit_micros
        if enforcement_enabled is not None:
            values["enforcement_enabled"] = enforcement_enabled

        result = await self._session.execute(
            update(EnterpriseBudget)
            .where(
                EnterpriseBudget.enterprise_id == enterprise_id,
                EnterpriseBudget.period_start == period_start,
            )
            .values(**values)
            .returning(EnterpriseBudget)
        )
        return result.scalar_one_or_none()
