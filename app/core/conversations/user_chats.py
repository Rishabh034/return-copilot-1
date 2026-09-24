"""Chat-shaped reads over `conversation`, for the internal admin surface.

Distinct from `ConversationRepository`, which answers "may this caller see this
conversation" for the chat UI. Nothing here is scoped to a caller — the routes
that use it are gated on ClickPost staff instead — and it counts things the
owner-scoped repository has no reason to count.

Two counting rules this module exists to keep straight:

  * A **chat** is one row in `conversation`.
  * A **message** is one row in `message`, and a **turn** is one human message
    plus everything the agent did to answer it.

They are different numbers and must never be conflated. A chat opened and
abandoned is 1 chat and 0 turns; a long chat is 1 chat and many. Cost lives in
`llm_usage_ledger` and is counted per turn, so a message count here can never
be compared against it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, Numeric, Select, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.conversations.conversation_model import Conversation
from app.core.conversations.message_model import Message
from app.core.billing.usage_ledger_model import LlmUsageLedger

# Ceiling on one page of chats. A staff caller asking for "everything" gets a
# bounded answer plus a flag, rather than a query that degrades as the table
# grows.
MAX_LIMIT = 50

# Tokens live inside the persisted LangChain response, not in a column:
# `message.token_count` is populated on only some rows, while usage_metadata is
# on every ai row that has one. Anything counting tokens reads it from here.
#
# Coverage is 96% — a minority of ai rows carry a JSON null here rather than an
# object — so these totals are a close floor, not an exact count.
_USAGE = Message.raw_response["data"]["usage_metadata"]


def max_page(total: int, limit: int) -> int:
    """How many pages `total` rows fill at this page size.

    Never returns 0: an empty result is one empty page, so a page picker always
    has a page 1 to sit on and `page <= max_page` stays true for the page the
    caller was just served.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    return max(1, -(-total // limit))


@dataclass(frozen=True)
class ChatRow:
    """One chat, with the counts a listing shows."""

    conversation_id: uuid.UUID
    enterprise_id: int
    user_id: int
    is_internal_user: bool
    internal_user_id: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    turn_count: int
    cost_micros: int
    total_tokens: int


class UserChats:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _scope(
        self,
        stmt: Select,
        *,
        enterprise_id: int,
        user_id: int | None,
        is_internal_user: bool | None,
        internal_user_id: str | None,
        frm: datetime | None = None,
        to: datetime | None = None,
    ) -> Select:
        """Apply the filters that were actually supplied.

        Each clause is added only when its argument is not None. Comparing a
        column to None instead produces `IS NULL`, which for the non-nullable
        `is_internal_user` matches nothing and reads as "this user has no
        chats" — the failure mode this method exists to avoid.
        """
        stmt = stmt.where(Conversation.enterprise_id == enterprise_id)
        if user_id is not None:
            stmt = stmt.where(Conversation.user_id == user_id)
        if is_internal_user is not None:
            stmt = stmt.where(Conversation.is_internal_user == is_internal_user)
        # Only meaningful for internal users; for a customer seat the column is
        # always null and filtering on it would exclude every row.
        if internal_user_id is not None:
            stmt = stmt.where(Conversation.internal_user_id == internal_user_id)
        if frm is not None:
            stmt = stmt.where(Conversation.created_at >= frm)
        if to is not None:
            stmt = stmt.where(Conversation.created_at < to)
        return stmt

    async def count_chats(
        self,
        *,
        enterprise_id: int,
        user_id: int | None = None,
        is_internal_user: bool | None = None,
        internal_user_id: str | None = None,
        frm: datetime | None = None,
        to: datetime | None = None,
    ) -> int:
        """How many chats match — chats, not messages.

        Takes the same window as `list_chats` and must keep taking it: the two
        are rendered side by side as "showing 25 of 312", so a count that
        ignores the date filter contradicts the rows beneath it and breaks the
        `has_more` calculation built on top.
        """
        stmt = self._scope(
            select(func.count()).select_from(Conversation),
            enterprise_id=enterprise_id,
            user_id=user_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            frm=frm,
            to=to,
        )
        return await self._session.scalar(stmt) or 0

    async def list_chats(
        self,
        *,
        enterprise_id: int,
        user_id: int | None = None,
        is_internal_user: bool | None = None,
        internal_user_id: str | None = None,
        frm: datetime | None = None,
        to: datetime | None = None,
        limit: int = 50,
        page: int = 1,
    ) -> list[ChatRow]:
        """One page of chats matching the filters, most recently updated first.

        `page` is 1-based, matching how it reads in a UI. A page past the end
        returns an empty list rather than an error — the caller may legitimately
        be holding a stale page number after rows were filtered out, and that is
        not a client mistake worth a 4xx.

        Counts come from correlated subqueries rather than from loading each
        conversation's messages: the listing needs two integers per row, and
        walking the relationship would pull every message body to produce them.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        offset = (max(1, page) - 1) * limit

        message_count = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        # A turn is one human message and the work that answered it, so human
        # rows are the countable thing. This is the figure that lines up with
        # `llm_usage_ledger`; message_count does not.
        turn_count = (
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == Conversation.id,
                Message.role == "human",
            )
            .correlate(Conversation)
            .scalar_subquery()
        )

        # Settled spend. One index probe per listed row on
        # ix_ledger_conversation, so effectively free. 0 for a conversation the
        # ledger never saw — most of them, since the ledger shipped later —
        # which is why `settled_turns` travels with it on the detail endpoint.
        cost = (
            select(func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0))
            .where(LlmUsageLedger.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        # Only the total here, and only from ai rows. Each extracted field is a
        # separate detoast of `raw_response`, so pulling the full five-way split
        # into a list of 50 costs about 4x what one field does. The split lives
        # on the detail endpoint where it is one row.
        total_tokens = (
            select(
                func.coalesce(
                    func.sum(cast(_USAGE["total_tokens"].astext, Numeric)), 0
                )
            )
            .where(
                Message.conversation_id == Conversation.id,
                Message.role == "ai",
            )
            .correlate(Conversation)
            .scalar_subquery()
        )

        stmt = self._scope(
            select(
                Conversation.id,
                Conversation.enterprise_id,
                Conversation.user_id,
                Conversation.is_internal_user,
                Conversation.internal_user_id,
                Conversation.title,
                Conversation.created_at,
                Conversation.updated_at,
                message_count.label("message_count"),
                turn_count.label("turn_count"),
                cost.label("cost_micros"),
                cast(total_tokens, Integer).label("total_tokens"),
            ),
            enterprise_id=enterprise_id,
            user_id=user_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            frm=frm,
            to=to,
        )
        stmt = stmt.order_by(
            Conversation.created_at.desc(), Conversation.id.desc()
        ).limit(limit).offset(offset)

        rows = (await self._session.execute(stmt)).all()
        return [
            ChatRow(
                conversation_id=row.id,
                enterprise_id=row.enterprise_id,
                user_id=row.user_id,
                is_internal_user=row.is_internal_user,
                internal_user_id=row.internal_user_id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
                message_count=row.message_count,
                turn_count=row.turn_count,
                cost_micros=row.cost_micros,
                total_tokens=row.total_tokens,
            )
            for row in rows
        ]

    async def get_chat(self, conversation_id: uuid.UUID) -> Conversation | None:
        """One chat with its messages loaded, or None.

        `selectinload` is not an optimisation here — it is required. The
        relationship is a lazy select, and triggering it on an `AsyncSession`
        raises `MissingGreenlet` rather than issuing the query.

        Not scoped to an enterprise: the id is a UUID, globally unique, and the
        caller is staff. The enterprise the chat actually belongs to is on the
        returned row, which is the value to report — never one passed in.
        """
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()


class ChatMetricsRepository:
    """Per-conversation figures, entirely from our own tables.

    These used to require a Langfuse round trip. They do not: tokens come from
    the persisted LangChain response, cost from the ledger, tool usage from the
    message columns. Measured, the DB answers in single-digit milliseconds
    against ~170ms for the trace API, and covers every conversation rather than
    only those traced after enterprise tagging shipped.

    What genuinely cannot be reconstructed here is per-call wall-clock latency
    and the error rate of turns that never persisted a row. `response_seconds`
    below is the closest the timestamps get, and its limits are documented
    there.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def message_side(self, conversation_id: uuid.UUID) -> dict[str, Any]:
        """Tokens, counts, latency and tool usage — in one pass over `message`.

        Deliberately one query rather than four. They all scan the same rows,
        and each `raw_response ->` chain is a separate detoast of a column
        averaging ~2KB, so gating the extraction once in `um` and aggregating
        off that reads each row a single time.
        """
        # Only ai rows carry usage_metadata. Gating here rather than in a WHERE
        # keeps human and tool rows available for the counts and the latency
        # window while never detoasting their raw_response.
        um = case(
            (Message.role == "ai", Message.raw_response["data"]["usage_metadata"]),
        ).label("um")

        m = (
            select(
                Message.conversation_id.label("conversation_id"),
                Message.role.label("role"),
                Message.seq.label("seq"),
                Message.created_at.label("created_at"),
                Message.tool_name.label("tool_name"),
                um,
            )
            .where(Message.conversation_id == conversation_id)
            .cte("m")
        )

        # PARTITION BY is redundant while the CTE holds one conversation, and
        # is present anyway: without it, reusing this shape for more than one
        # conversation silently measures the gap between two unrelated chats.
        gaps = select(
            m.c.role.label("role"),
            func.extract(
                "epoch",
                m.c.created_at
                - func.lag(m.c.created_at).over(
                    partition_by=m.c.conversation_id, order_by=m.c.seq
                ),
            ).label("gap"),
        ).cte("g")

        response_seconds = (
            select(func.round(cast(func.sum(gaps.c.gap), Numeric), 2))
            # A gap landing on a human row is the user thinking, not the system
            # working. Including it makes an idle conversation look slow.
            .where(gaps.c.role != "human")
            .scalar_subquery()
        )

        tool_counts = (
            select(m.c.tool_name.label("tn"), func.count().label("calls"))
            .where(m.c.role == "tool", m.c.tool_name.is_not(None))
            .group_by(m.c.tool_name)
            .subquery()
        )
        tools = select(
            func.jsonb_object_agg(tool_counts.c.tn, tool_counts.c.calls)
        ).scalar_subquery()

        def tok(key: str):
            # numeric, not integer: a provider emitting 12.0 or "12" would make
            # an ::int cast raise, and because the cast sits inside the
            # aggregate one bad row would take out the whole conversation's
            # metrics rather than just its own contribution.
            return func.coalesce(func.sum(cast(m.c.um[key].astext, Numeric)), 0)

        def cache(key: str):
            return func.coalesce(
                func.sum(cast(m.c.um["input_token_details"][key].astext, Numeric)), 0
            )

        row = (
            await self._session.execute(
                select(
                    tok("input_tokens").label("input_tokens"),
                    tok("output_tokens").label("output_tokens"),
                    tok("total_tokens").label("total_tokens"),
                    cache("cache_read").label("cache_read"),
                    cache("cache_creation").label("cache_creation"),
                    func.count().label("messages"),
                    func.count().filter(m.c.role == "human").label("turns"),
                    response_seconds.label("response_seconds"),
                    tools.label("tools"),
                ).select_from(m)
            )
        ).one()

        return {
            "tokens": {
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "total_tokens": int(row.total_tokens),
                "cache_read_tokens": int(row.cache_read),
                "cache_creation_tokens": int(row.cache_creation),
            },
            "messages": row.messages,
            "turns": row.turns,
            # None, not 0.0: a chat with fewer than two rows has no measurable
            # interval, which is not the same as having responded instantly.
            "response_seconds": (
                float(row.response_seconds) if row.response_seconds is not None else None
            ),
            "tools": [
                {"tool_name": name, "calls": calls}
                for name, calls in sorted(
                    (row.tools or {}).items(), key=lambda kv: -kv[1]
                )
            ],
        }

    async def cost(self, conversation_id: uuid.UUID) -> dict[str, Any]:
        """Settled spend, from the ledger.

        The ledger is the authority on money and is deliberately independent of
        the message rows — `conversation_id` is not a foreign key, so a row
        outlives the conversation it describes.

        `settled_turns` is the completeness check: where the ledger has seen a
        conversation it equals the human-message count exactly, so a mismatch
        means some turns were never metered.
        """
        row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0).label("cost"),
                    func.count().label("settled_turns"),
                    func.count()
                    .filter(LlmUsageLedger.pricing_failed.is_(True))
                    .label("unpriced"),
                    func.coalesce(
                        func.sum(LlmUsageLedger.secondary_input_tokens), 0
                    ).label("sec_in"),
                    func.coalesce(
                        func.sum(LlmUsageLedger.secondary_output_tokens), 0
                    ).label("sec_out"),
                    func.max(LlmUsageLedger.model).label("model"),
                    func.max(LlmUsageLedger.secondary_model).label("secondary_model"),
                ).where(LlmUsageLedger.conversation_id == conversation_id)
            )
        ).one()
        cost_micros = int(row.cost)
        return {
            "cost_micros": cost_micros,
            "cost_usd": round(cost_micros / 1_000_000, 6),
            "settled_turns": row.settled_turns,
            "unpriced_turns": row.unpriced,
            "model": row.model,
            # The scratchpad summariser. Its calls never produce message rows,
            # so these tokens exist nowhere else — do not add them to the
            # `tokens` block above, which counts only what the agent itself
            # sent and received.
            "secondary_model": row.secondary_model,
            "secondary_input_tokens": int(row.sec_in),
            "secondary_output_tokens": int(row.sec_out),
        }

    async def summary(self, conversation_id: uuid.UUID) -> dict[str, Any]:
        """The metrics block the transcript endpoint returns. Two round trips."""
        message_side = await self.message_side(conversation_id)
        return {
            **message_side,
            "cost": await self.cost(conversation_id),
        }
