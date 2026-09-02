"""Append-only audit of every /integrations/query request."""

from __future__ import annotations

import logging
import uuid
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.rr_log_analyst.models.integration_request_log import IntegrationRequestLog
from app.rr_log_analyst.services.pricing import UnknownModelError, turn_cost

logger = logging.getLogger(__name__)

# Postgres text columns are unbounded, but a runaway answer or a stack trace in
# an error message is not worth an unbounded row. Both limits sit far above any
# real value.
_MAX_TEXT_CHARS = 200_000
_MAX_ERROR_CHARS = 4_000


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit] + "… [truncated]"


class IntegrationLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        api_key_id: uuid.UUID,
        request_id: uuid.UUID,
        license_key_hash: str,
        query: str,
        status: str,
        http_status: int = 200,
        latency_ms: int = 0,
        enterprise_id: int | None = None,
        enterprise_name: str | None = None,
        answer: str | None = None,
        reasoning: str | None = None,
        tools_used: list[str] | None = None,
        model: str | None = None,
        usage: Mapping[str, int] | None = None,
        secondary_model: str | None = None,
        secondary_usage: Mapping[str, int] | None = None,
        error_message: str | None = None,
    ) -> IntegrationRequestLog:
        """Price a finished request and append it. Returns the row.

        Pricing failure is deliberately not fatal, for the same reason it is not
        in usage_ledger_repo: the work has already happened, and refusing to
        record it would lose the token counts entirely. The row is written with
        cost zero and `pricing_failed` set, which keeps it re-priceable from
        `model` and the counts and makes the gap visible rather than silent.
        """
        usage = usage or {}
        secondary_usage = secondary_usage or {}

        cost_micros = 0
        pricing_failed = False
        if model and (usage or secondary_usage):
            try:
                cost_micros = turn_cost(
                    model=model,
                    usage=usage,
                    secondary_model=secondary_model,
                    secondary_usage=secondary_usage,
                ).total_micros
            except UnknownModelError:
                logger.exception(
                    "No pricing for model=%r secondary=%r; recording integration "
                    "request at cost 0 with pricing_failed=true (request_id=%s)",
                    model,
                    secondary_model,
                    request_id,
                )
                pricing_failed = True

        row = IntegrationRequestLog(
            id=uuid.uuid4(),
            api_key_id=api_key_id,
            request_id=request_id,
            license_key_hash=license_key_hash,
            enterprise_id=enterprise_id,
            enterprise_name=enterprise_name,
            query=_clip(query, _MAX_TEXT_CHARS),
            answer=_clip(answer, _MAX_TEXT_CHARS),
            reasoning=_clip(reasoning, _MAX_TEXT_CHARS),
            tools_used=tools_used or [],
            model=model,
            input_tokens=usage.get("input_tokens") or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
            cache_creation_tokens=usage.get("cache_creation_input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            secondary_model=secondary_model,
            secondary_input_tokens=secondary_usage.get("input_tokens") or 0,
            secondary_output_tokens=secondary_usage.get("output_tokens") or 0,
            cost_micros=cost_micros,
            pricing_failed=pricing_failed,
            status=status,
            error_message=_clip(error_message, _MAX_ERROR_CHARS),
            http_status=http_status,
            latency_ms=latency_ms,
        )
        self._session.add(row)
        await self._session.flush()
        return row
