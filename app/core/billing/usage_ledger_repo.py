"""Append-only record of what every turn used and cost."""

from __future__ import annotations

import logging
import uuid
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing.usage_ledger_model import LlmUsageLedger
from app.core.billing.pricing import UnknownModelError, turn_cost

logger = logging.getLogger(__name__)


class UsageLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_turn(
        self,
        *,
        enterprise_id: int,
        user_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID | None,
        agent_slug: str,
        model: str,
        usage: Mapping[str, int] | None,
        secondary_model: str | None = None,
        secondary_usage: Mapping[str, int] | None = None,
        counted_against_pool: bool = True,
    ) -> LlmUsageLedger:
        """Price a finished turn and append it. Returns the row.

        Pricing failure is deliberately not fatal. The turn already completed
        and the user already has their answer; refusing to record it would lose
        the token counts entirely. Instead the row is written with cost zero and
        `pricing_failed` set, which keeps it re-priceable later from `model` and
        the counts and makes the gap visible rather than silent.
        """
        usage = usage or {}
        secondary_usage = secondary_usage or {}

        pricing_failed = False
        try:
            cost = turn_cost(
                model=model,
                usage=usage,
                secondary_model=secondary_model,
                secondary_usage=secondary_usage,
            )
            cost_micros = cost.total_micros
        except UnknownModelError:
            logger.exception(
                "No pricing for model=%r secondary=%r; recording turn at cost 0 "
                "with pricing_failed=true (conversation_id=%s)",
                model,
                secondary_model,
                conversation_id,
            )
            cost_micros = 0
            pricing_failed = True

        row = LlmUsageLedger(
            id=uuid.uuid4(),
            enterprise_id=enterprise_id,
            user_id=user_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            conversation_id=conversation_id,
            agent_slug=agent_slug,
            message_id=message_id,
            model=model,
            input_tokens=usage.get("input_tokens") or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
            cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            secondary_model=secondary_model,
            secondary_input_tokens=secondary_usage.get("input_tokens") or 0,
            secondary_output_tokens=secondary_usage.get("output_tokens") or 0,
            cost_micros=cost_micros,
            counted_against_pool=counted_against_pool,
            pricing_failed=pricing_failed,
        )
        self._session.add(row)
        await self._session.flush()
        return row
