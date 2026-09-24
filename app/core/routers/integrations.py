from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from app.clients.clickpost import ClickpostAuth
from app.config import settings
from app.db import AsyncSessionLocal
from app.core.auth.api_key import require_api_key
from app.exceptions import AppError, AuthError, BadRequestError
from app.core import registry
from app.core.runtime.runner import AgentRunner
from app.core.runtime.tool_results import tool_result_store
from app.core.integrations.integration_api_key_model import IntegrationApiKey
from app.core.integrations.integration_request_log_model import IntegrationRequestLog
from app.core.repositories import (
    IntegrationKeyRepository,
    IntegrationLogRepository,
    KeyBudgetState,
)
from app.core.integrations.license_key_check import (
    EnterpriseIdentity,
    InvalidLicenseKeyError,
    resolve_license_key,
)

logger = logging.getLogger(__name__)

# Service-to-service callers do not choose an agent: this endpoint has always
# answered as the allocation agent, and existing API keys depend on that.
SPEC = registry.get("allocation")

router = APIRouter()


class IntegrationQueryRequest(BaseModel):
    key: str = Field(..., description="Clickpost enterprise license key (unique_id UUID)")
    # Matches SendMessageRequest: a caller pasting a log excerpt hits the same
    # wall here as on /messages/, which is what raised that limit to 25k.
    message: str = Field(..., min_length=1, max_length=25_000)
    message_time: str | None = None


class IntegrationQueryResponse(BaseModel):
    request_id: str
    answer: str
    reasoning: str | None
    tools_used: list[str]
    usage: dict | None


@dataclass
class TurnResult:
    """Everything one agent run produced, including a run that failed.

    A failed run still burned tokens, so the usage figures are collected on
    every path and the error is returned rather than raised — the caller writes
    the audit row and settles the money first, and decides the HTTP status
    after.
    """

    answer: str = ""
    reasoning: str = ""
    tools_used: list[str] = field(default_factory=list)
    usage: dict | None = None
    secondary_usage: dict | None = None
    error: dict | None = None


def _validated_license_key(key: str) -> str:
    key = key.strip()
    try:
        uuid.UUID(key)
    except ValueError:
        raise BadRequestError("Invalid license key")
    return key


def _license_key_hash(key: str) -> str:
    """The license key is a live Clickpost credential; only its digest is stored."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@router.post("/query", response_model=None)
async def query(
    body: IntegrationQueryRequest,
    request: Request,
    api_key: IntegrationApiKey = Depends(require_api_key),
) -> IntegrationQueryResponse:
    """Single-shot agent run for service-to-service callers.

    Two credentials, answering two different questions. The `X-API-Key` header
    identifies the *calling service*: it carries the spend cap, the usage
    counters, and the audit trail. The `key` field is the Clickpost enterprise
    license key, which decides which enterprise's data this one question may
    reach — a single caller passes a different one per request.

    Downstream, the license key authenticates in place of a dashboard token:
    Clickpost's v4/v5 profile decorators accept `?key=<unique_id>` and resolve
    the enterprise from it. Nothing about the calling service reaches Clickpost.

    Takes no enterprise id or name. The license key already scopes every
    downstream lookup, so those fields would only ever reach the prompt as
    caller-asserted text — a caller could name an enterprise whose data the key
    does not grant, and the model would repeat it back.

    Deliberately carries no dashboard token or agent-JWT dependency, and must
    keep it that way: callers are other services, which have no user identity,
    so resolve_identity has nothing to resolve here. Do not add it to this route
    for consistency with the /messages/, /conversations/ and /feedback/ routers
    — it would lock out every integration caller the moment AGENT_JWT_ENABLED is
    turned on.

    Not conversational and not streamed. Each request is one complete
    question-and-answer, recorded in `integration_request_log` and identified by
    the `request_id` in the response.
    """
    started = time.perf_counter()
    request_id = uuid.uuid4()
    license_key = _validated_license_key(body.key)
    license_key_hash = _license_key_hash(license_key)
    message = body.message.strip()
    client = getattr(request.app.state, "clickpost_client", None)

    # Filled in by the pre-flight below, so every row it writes after that point
    # names the enterprise. Rows written before it stay unattributed, which is
    # correct: at that point we do not know who the key belongs to.
    identity = EnterpriseIdentity()

    async def record(**kwargs) -> None:
        await _record(
            api_key_id=api_key.id,
            request_id=request_id,
            license_key_hash=license_key_hash,
            enterprise_id=identity.enterprise_id,
            enterprise_name=identity.enterprise_name,
            query=message,
            latency_ms=int((time.perf_counter() - started) * 1000),
            **kwargs,
        )

    # One call that both rejects a key Clickpost does not know — so a typo costs
    # a round trip rather than a model run — and names the enterprise for the
    # audit trail. Fails open: an empty identity, never a refused request.
    try:
        identity = await resolve_license_key(
            client, license_key, key_hash=license_key_hash
        )
    except InvalidLicenseKeyError as exc:
        await record(
            status=IntegrationRequestLog.INVALID_LICENSE_KEY,
            http_status=401,
            error_message=str(exc),
        )
        raise AuthError("Invalid Clickpost license key.")

    # A hold against this key's monthly cap, because the request's real cost is
    # unknown until it finishes. Committed before the agent runs: every request
    # for this key updates this row, so holding the transaction open across the
    # model call would serialize the caller behind a row lock. Inert while the
    # key has no cap, which is every key until one is provisioned.
    async with AsyncSessionLocal() as session:
        budget = await IntegrationKeyRepository(session).reserve(
            api_key_id=api_key.id,
            estimate_micros=settings.integration_reservation_estimate_micros,
        )
        await session.commit()

    if not budget.allowed:
        await record(
            status=IntegrationRequestLog.BLOCKED,
            http_status=429,
            error_message=(
                f"Monthly cap reached for API key {api_key.name}: "
                f"spent={budget.spent_micros} reserved={budget.reserved_micros} "
                f"limit={budget.limit_micros}"
            ),
        )
        logger.warning(
            "Integration request blocked by monthly cap key=%s prefix=%s "
            "spent=%s limit=%s",
            api_key.name,
            api_key.key_prefix,
            budget.spent_micros,
            budget.limit_micros,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "budget_integration_key",
                "message": (
                    "This integration key has used its budget for the month. "
                    "Raise the limit or wait for the period to roll over."
                ),
                "limit_micros": budget.limit_micros,
                "spent_micros": budget.spent_micros,
                "remaining_micros": budget.remaining_micros,
                "period_end": budget.period_end.isoformat(),
            },
            headers={"Retry-After": str(_seconds_until(budget.period_end))},
        )

    runner = AgentRunner(
        clickpost_client=client,
        build_tools=SPEC.build_tools,
        agent_slug=SPEC.slug,
        runtime=SPEC.runtime,
        presentation=SPEC.presentation,
        tool_groups=SPEC.tool_groups,
        output_filter=SPEC.output_filter,
    )
    events = runner.handle_message_stream(
        messages=[HumanMessage(content=message)],
        system_prompt=SPEC.build_system_prompt(),
        context_note=SPEC.build_context_note(message_time=body.message_time),
        auth=ClickpostAuth(key=license_key),
        conversation_id=str(request_id),
    )

    try:
        result = await _collect(events, str(request_id))
    except Exception as exc:
        # The run died outright rather than reporting an error event, so there
        # are no usage figures to charge. Drop the hold and record the attempt.
        await _release(api_key.id, budget)
        await record(
            status=IntegrationRequestLog.ERROR,
            http_status=500,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise

    failed = result.error is not None
    await _settle(
        api_key_id=api_key.id,
        budget=budget,
        request_id=request_id,
        license_key_hash=license_key_hash,
        identity=identity,
        query=message,
        result=result,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )

    if failed:
        logger.error("Integration query failed: %s", result.error)
        raise AppError(
            result.error.get("message")
            or "The assistant could not complete the request."
        )

    return IntegrationQueryResponse(
        request_id=str(request_id),
        answer=result.answer,
        reasoning=result.reasoning or None,
        tools_used=result.tools_used,
        usage=result.usage,
    )


def _seconds_until(period_end) -> int:
    return max(
        0,
        int(
            (
                datetime.combine(period_end, datetime.min.time(), timezone.utc)
                - datetime.now(timezone.utc)
            ).total_seconds()
        ),
    )


async def _collect(events, conversation_id: str) -> TurnResult:
    result = TurnResult()
    buffer = ""

    try:
        async for event_type, data in events:
            if event_type == "text_delta":
                buffer += data.get("delta", "")
            elif event_type == "thinking_delta":
                result.reasoning += data.get("delta", "")
            elif event_type == "tool_start":
                result.tools_used.append(data["tool"])
            elif event_type == "message_complete":
                # An assistant turn just closed: whatever streamed since the
                # last one is that turn's text. Only the last turn is the answer
                # — earlier ones are the agent talking between tool calls.
                if isinstance(data["message"], AIMessage):
                    result.answer = buffer
                buffer = ""
            elif event_type == "done":
                result.usage = data.get("usage")
                result.secondary_usage = data.get("secondary_usage")
            elif event_type == "error":
                result.error = data
    finally:
        tool_result_store.purge_conversation(conversation_id)

    return result


async def _settle(
    *,
    api_key_id: uuid.UUID,
    budget: KeyBudgetState,
    request_id: uuid.UUID,
    license_key_hash: str,
    identity: EnterpriseIdentity,
    query: str,
    result: TurnResult,
    latency_ms: int,
) -> None:
    """Close the books on one request: price it, record it, charge the key.

    Both in one session and one commit, because the audit row and the counter
    must not disagree about whether a request happened or what it cost. The
    ledger row is written first and its priced figure is what settles the hold,
    so there is exactly one place pricing happens.
    """
    failed = result.error is not None
    async with AsyncSessionLocal() as session:
        row = await IntegrationLogRepository(session).record(
            api_key_id=api_key_id,
            request_id=request_id,
            license_key_hash=license_key_hash,
            enterprise_id=identity.enterprise_id,
            enterprise_name=identity.enterprise_name,
            query=query,
            answer=result.answer or None,
            reasoning=result.reasoning or None,
            tools_used=result.tools_used,
            model=SPEC.runtime.model,
            usage=result.usage,
            secondary_model=(
                SPEC.runtime.secondary_model if result.secondary_usage else None
            ),
            secondary_usage=result.secondary_usage,
            status=(
                IntegrationRequestLog.ERROR if failed else IntegrationRequestLog.OK
            ),
            http_status=500 if failed else 200,
            latency_ms=latency_ms,
            error_message=(result.error or {}).get("message") if failed else None,
        )
        # A failed run still spent whatever it spent before it failed; the hold
        # is replaced by that measured figure, not released.
        await IntegrationKeyRepository(session).settle(
            api_key_id=api_key_id,
            estimate_micros=budget.estimate_micros,
            actual_micros=row.cost_micros,
        )
        await session.commit()


async def _release(api_key_id: uuid.UUID, budget: KeyBudgetState) -> None:
    """Drop a hold for a request that never reached settlement."""
    try:
        async with AsyncSessionLocal() as session:
            await IntegrationKeyRepository(session).release(
                api_key_id=api_key_id, estimate_micros=budget.estimate_micros
            )
            await session.commit()
    except Exception:
        # Best effort: a leaked hold clears at the period roll and is never
        # worth failing a request that has already finished.
        logger.exception("Failed to release integration hold api_key_id=%s", api_key_id)


async def _record(**kwargs) -> None:
    """Append an audit row on a path that has no money to settle.

    Best effort: a request that was already refused must not turn into a 500
    because the recording of that refusal failed.
    """
    try:
        async with AsyncSessionLocal() as session:
            await IntegrationLogRepository(session).record(**kwargs)
            await session.commit()
    except Exception:
        logger.exception(
            "Failed to record integration request request_id=%s",
            kwargs.get("request_id"),
        )
