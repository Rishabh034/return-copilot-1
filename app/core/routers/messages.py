from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import update

from datetime import datetime
from app.db import AsyncSessionLocal
from app.clients.clickpost import ClickpostAuth
from app.core.auth.agent_token import AuthContext, resolve_identity_context
from app.clients.observability import (
    langfuse_identity,
    mark_current_span_error,
    trace_span,
)
from app.exceptions import AppError, NotFoundError, BadRequestError
from app.utilities.alerts import alert_slack
from app.core.request_spec import agent_spec
from app.core.runtime.orchestrator import choose_agent
from app.core.runtime.runner import AgentRunner
from app.core.spec import AgentSpec
from app.core.runtime.context import merge_usage, serialize_usage
from app.core.auth.actor import (
    resolve_body_internal_actor,
    resolve_internal_actor,
)
from app.core.conversations.store import _extract_thinking, utc_now_iso_ms
from app.core.billing.budget_block_event_model import BudgetBlockEvent
from app.core.conversations.message_model import Message
from app.core.repositories import (
    BudgetRepository,
    BudgetState,
    ConversationRepository,
    UsageLedgerRepository,
    UserTokenRepository,
)
from app.core.conversations.message_mapper import build_partial_ai_message
from app.core.conversations.messages_schema import SendMessageRequest
from app.core.conversations.title import generate_conversation_title
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def _route_turn(
    entry: AgentSpec,
    *,
    message: str,
    conversation_id: str | None,
    user_id: int,
    enterprise_id: int,
    is_internal_user: bool,
    internal_user_id: str | None,
) -> tuple[AgentSpec, dict[str, int] | None]:
    """Which agent runs, before quota. A missing conversation is a 404 here."""
    if not entry.routes:
        return entry, None
    locked_slug: str | None = None
    if conversation_id:
        try:
            requested_id = uuid.UUID(conversation_id)
        except ValueError:
            raise BadRequestError("Invalid conversation_id")
        async with AsyncSessionLocal() as session:
            repo = ConversationRepository(session)
            state = await repo.routing_state(
                requested_id,
                user_id=user_id,
                enterprise_id=enterprise_id,
                internal_user_id=internal_user_id,
                is_internal_user=is_internal_user,
            )
            if state is None:
                raise NotFoundError("Conversation not found")
            slug, has_messages = state
        if has_messages:
            locked_slug = slug
        else:
            # Classify after the read commits, so a model call does not sit
            # inside the database transaction.
            spec, usage = await choose_agent(entry, message, locked_slug=None)
            if slug != spec.slug:
                async with AsyncSessionLocal() as session:
                    await ConversationRepository(session).set_agent_slug(
                        requested_id, spec.slug
                    )
                    await session.commit()
            logger.info(
                "Routed new conversation_id=%s agent=%s",
                conversation_id,
                spec.slug,
            )
            return spec, usage
    spec, usage = await choose_agent(entry, message, locked_slug=locked_slug)
    logger.info(
        "Routed message agent=%s locked=%s conversation_id=%s",
        spec.slug,
        locked_slug,
        conversation_id,
    )
    return spec, usage


async def _settle_turn(
    *,
    user_id: int,
    enterprise_id: int,
    is_internal_user: bool,
    internal_user_id: str | None,
    conversation_id: uuid.UUID,
    human_message_id: uuid.UUID,
    input_tokens: int,
    output_tokens: int,
    side_input_tokens: int = 0,
    usage: dict | None = None,
    secondary_usage: dict | None = None,
    budget_state: BudgetState | None = None,
    model: str,
    secondary_model: str,
    agent_slug: str,
) -> None:
    """Close the books on one turn: rate-limit quota, cost ledger, spend cap.

    All three in one session and one commit, because they must not disagree
    about whether a turn happened.

    Note the two different notions of "tokens used" here, which are deliberately
    not the same number. `input_tokens` is the rate limiter's figure - the
    largest prompt across iterations, since every iteration resends the previous
    one's context and summing would bill it repeatedly. `usage` is the sum
    across iterations, which is what Anthropic actually charges and therefore
    the only correct basis for money. Using one where the other belongs
    under-counts spend by roughly an order of magnitude.
    """
    async with AsyncSessionLocal() as session:
        has_token_usage = input_tokens > 0 or output_tokens > 0 or side_input_tokens > 0

        if has_token_usage:
            repo = UserTokenRepository(session)
            await session.execute(
                update(Message)
                .where(Message.id == human_message_id)
                .values(token_count=input_tokens)
            )
            await repo.update_tokens(
                user_id=user_id,
                enterprise_id=enterprise_id,
                agent_slug=agent_slug,
                is_internal_user=is_internal_user,
                internal_user_id=internal_user_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                side_input_tokens=side_input_tokens,
            )

        cost_micros = 0
        if usage or secondary_usage:
            ledger_row = await UsageLedgerRepository(session).record_turn(
                enterprise_id=enterprise_id,
                user_id=user_id,
                is_internal_user=is_internal_user,
                internal_user_id=internal_user_id,
                conversation_id=conversation_id,
                message_id=human_message_id,
                agent_slug=agent_slug,
                model=model,
                usage=usage,
                secondary_model=secondary_model if secondary_usage else None,
                secondary_usage=secondary_usage,
                # Support activity is attributed to the customer for reporting
                # but never charged against their cap.
                counted_against_pool=not is_internal_user,
            )
            cost_micros = ledger_row.cost_micros

        # Always settle a hold we placed, even for a turn that produced nothing
        # - otherwise the reservation leaks until the period rolls over.
        if budget_state is not None and budget_state.allowed:
            await BudgetRepository(session).settle(
                enterprise_id=enterprise_id,
                agent_slug=agent_slug,
                period_start=budget_state.period_start,
                estimate_micros=budget_state.estimate_micros,
                actual_micros=cost_micros,
            )

        await session.commit()


async def _release_budget_hold(
    *, enterprise_id: int, agent_slug: str, budget_state: BudgetState | None
) -> None:
    """Drop a hold for a turn that never reached settlement."""
    if budget_state is None or not budget_state.allowed:
        return
    try:
        async with AsyncSessionLocal() as session:
            await BudgetRepository(session).release(
                enterprise_id=enterprise_id,
                agent_slug=agent_slug,
                period_start=budget_state.period_start,
                estimate_micros=budget_state.estimate_micros,
            )
            await session.commit()
    except Exception:
        # Best effort: a leaked hold is recovered by the sweeper and is never
        # worth failing a request that has already finished.
        logger.exception(
            "Failed to release budget hold enterprise_id=%s", enterprise_id
        )


def _stamp_message_time(message: BaseMessage, message_time: str | None = None) -> None:
    """Ensure a user/assistant message carries a display message_time."""
    if not isinstance(message, (HumanMessage, AIMessage)):
        return
    if not message.additional_kwargs.get("message_time"):
        message.additional_kwargs["message_time"] = message_time or utc_now_iso_ms()


async def _persist_partial(
    conversation_id: uuid.UUID,
    *,
    user_id: int,
    enterprise_id: int,
    internal_user_id: str | None,
    is_internal_user: bool,
    text: str,
) -> None:
    """Append a display-only partial assistant row in its own session.

    Runs during stream cancellation, so it must not share the unwinding
    streaming session. Best-effort: a failure here must not mask the cancel.
    """
    try:
        async with AsyncSessionLocal() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_owned(
                conversation_id,
                user_id=user_id,
                enterprise_id=enterprise_id,
                internal_user_id=internal_user_id,
                is_internal_user=is_internal_user,
            )
            if conversation is None:
                return
            partial = build_partial_ai_message(text)
            _stamp_message_time(partial)
            seq = await repo.next_seq(conversation_id)
            await repo.append_message(
                conversation, seq=seq, message=partial, status="partial"
            )
            await session.commit()
            logger.info(
                "Persisted partial assistant turn conversation_id=%s seq=%d chars=%d",
                conversation_id,
                seq,
                len(text),
            )
    except Exception:
        logger.exception(
            "Failed to persist partial turn conversation_id=%s", conversation_id
        )


async def _resolve_identity(
    body: SendMessageRequest,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str, AuthContext | None]:
    """Identity plus the verified claims, which this router needs for billing.

    Whether a turn counts against the enterprise pool is decided from the
    claims where clickpost signs them, so the context has to survive as far as
    `resolve_internal_actor` rather than being dropped here.
    """
    return await resolve_identity_context(
        user_id=body.user_id,
        enterprise_id=body.enterprise_id,
        authorization=authorization,
    )


@router.post("/messages/")
async def send_message(
    body: SendMessageRequest,
    request: Request,
    identity: tuple[int, int, str, AuthContext | None] = Depends(_resolve_identity),
):
    user_id, enterprise_id, token, auth_ctx = identity
    # The URL is the entry. When that entry routes several agents, the first
    # message picks one and quota, budget, and the runner all follow it.
    entry = agent_spec(request)
    # which conversation list the chat is filed under follows the client's own
    # flags, because that is what GET /conversations/ lists by, filing it under
    # the signed identity instead of hiding it
    # Billing below never reads these.
    owner_is_internal_user, owner_internal_user_id = resolve_body_internal_actor(
        body_is_internal_user=body.is_internal_user,
        body_internal_user_id=body.internal_user_id,
    )
    # Signed claims win over the body here — this flag decides whether the turn
    # is billed to the enterprise's pool. See resolve_internal_actor.
    is_internal_user, internal_user_id = resolve_internal_actor(
        ctx=auth_ctx,
        body_is_internal_user=body.is_internal_user,
        body_internal_user_id=body.internal_user_id,
    )
    SPEC, route_usage = await _route_turn(
        entry,
        message=body.message,
        conversation_id=body.conversation_id,
        user_id=user_id,
        enterprise_id=enterprise_id,
        is_internal_user=owner_is_internal_user,
        internal_user_id=owner_internal_user_id,
    )

    async with AsyncSessionLocal() as session:
        token_repo = UserTokenRepository(session)
        allowed = await token_repo.can_start(
            user_id=user_id,
            enterprise_id=enterprise_id,
            agent_slug=SPEC.slug,
            is_internal_user=owner_is_internal_user,
            internal_user_id=owner_internal_user_id,
        )
        quota = await token_repo.get_owned(
            user_id=user_id,
            enterprise_id=enterprise_id,
            agent_slug=SPEC.slug,
            is_internal_user=owner_is_internal_user,
            internal_user_id=owner_internal_user_id,
        )
        await session.commit()

    if not allowed:
        reset_at = quota.reset_at if quota is not None else None
        can_claim_extra_tokens = bool(
            quota is not None
            and not quota.extra_token_used
            and (
                quota.used_input_tokens + quota.used_output_tokens
                >= quota.max_input_tokens + quota.max_output_tokens
            )
        )
        retry_after = (
            max(0, int((reset_at - datetime.now(timezone.utc)).total_seconds()))
            if reset_at is not None
            else 0
        )
        # Standard {"detail": {...}} shape; ResponseEnvelopeMiddleware lifts
        # "message" into meta.message and puts the rest under data.
        async with AsyncSessionLocal() as session:
            await BudgetRepository(session).record_block(
                enterprise_id=enterprise_id,
                user_id=user_id,
                is_internal_user=is_internal_user,
                limit_kind=BudgetBlockEvent.USER_TOKEN_WINDOW,
                agent_slug=SPEC.slug,
            )
            await session.commit()

        # Standard {"detail": {...}} shape; ResponseEnvelopeMiddleware lifts
        # "message" into meta.message and puts the rest under data.
        raise HTTPException(
            status_code=429,
            detail={
                "code": "quota_user_window",
                "message": "Token limit reached.",
                "reset_at": reset_at.isoformat() if reset_at else None,
                "can_claim_extra_tokens": can_claim_extra_tokens,
            },
            headers={"Retry-After": str(retry_after)},
        )

    clickpost_client = getattr(request.app.state, "clickpost_client", None)
    shared_clients = getattr(request.app.state, "shared_clients", {})
    enterprise_name = body.enterprise_name if body.enterprise_name else None
    username = body.username if body.username else None
    raw_message = body.message
    display_message = raw_message.strip()

    system_prompt = SPEC.build_system_prompt()
    context_note = SPEC.build_context_note(
        enterprise_id=enterprise_id,
        message_time=body.message_time,
        enterprise_name=enterprise_name,
        username=username,
    )
    runner = AgentRunner(
        clickpost_client=clickpost_client,
        build_tools=SPEC.build_tools,
        agent_slug=SPEC.slug,
        runtime=SPEC.runtime,
        presentation=SPEC.presentation,
        tool_groups=SPEC.tool_groups,
        output_filter=SPEC.output_filter,
        clients={name: shared_clients[name] for name in SPEC.clients if name in shared_clients},
    )
    # Ownership / existence check up front so existing-but-unowned conversations
    # get a real 404 instead of a 200 stream carrying an error event. The replay
    # history loaded here (completed turns only) is what the agent loop sees.
    # It must stay above the budget hold below: anything that raises between
    # `reserve` and the stream strands the hold until the month rolls over.
    is_new_conversation = body.conversation_id is None
    async with AsyncSessionLocal() as session:
        repo = ConversationRepository(session)
        if body.conversation_id:
            try:
                requested_id = uuid.UUID(body.conversation_id)
            except ValueError:
                raise BadRequestError("Invalid conversation_id")
            conversation = await repo.get_owned(
                requested_id,
                user_id=user_id,
                enterprise_id=enterprise_id,
                internal_user_id=owner_internal_user_id,
                is_internal_user=owner_is_internal_user,
            )
            if conversation is None:
                raise NotFoundError("Conversation not found")
            conversation_id = str(conversation.id)
            messages = await repo.load_messages(conversation.id)
        else:
            conversation_id = str(uuid.uuid4())
            messages = []

    human_kwargs: dict = {
        "message_time": body.message_time or utc_now_iso_ms(),
    }

    human_message = HumanMessage(
        content=display_message,
        additional_kwargs=human_kwargs,
    )

    # enterprise spend cap, hold is placed before the turn runs because the
    # real cost is not known until it finishes; it is released and replaced with
    # the measured figure at settlement. Internal support activity is recorded
    # against the enterprise but never charged to it, so it takes no hold.
    #
    # with enforcement_enabled false the hold is still placed and settled, it
    # just never refuses - so a leaked hold is invisible until the flag flips.
    #
    # placed last, after every check that can refuse the request, so the only
    # thing left between here and the stream's settle-or-release is returning
    # the response.
    budget_state: BudgetState | None = None
    if not is_internal_user:
        async with AsyncSessionLocal() as session:
            budget_repo = BudgetRepository(session)
            budget_state = await budget_repo.reserve(
                enterprise_id=enterprise_id, agent_slug=SPEC.slug
            )
            if not budget_state.allowed:
                await budget_repo.record_block(
                    enterprise_id=enterprise_id,
                    user_id=user_id,
                    is_internal_user=is_internal_user,
                    limit_kind=BudgetBlockEvent.ENTERPRISE_BUDGET,
                    agent_slug=SPEC.slug,
                    state=budget_state,
                )
            await session.commit()

        if not budget_state.allowed:
            # Distinct from the token-window 429 above: that one clears by
            # itself in a few hours, this one needs an admin to raise the cap or
            # the month to end. The client cannot say the right thing to the
            # user without being able to tell them apart.
            retry_after = max(
                0,
                int(
                    (
                        datetime.combine(
                            budget_state.period_end, datetime.min.time(), timezone.utc
                        )
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                ),
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "budget_enterprise",
                    "message": (
                        "Your organisation has used its ClickHelp budget for this "
                        "month. Ask an administrator to raise the limit."
                    ),
                    "limit_micros": budget_state.limit_micros,
                    "spent_micros": budget_state.spent_micros,
                    "remaining_micros": budget_state.remaining_micros,
                    "period_end": budget_state.period_end.isoformat(),
                },
                headers={"Retry-After": str(retry_after)},
            )
    messages.append(human_message)

    async def event_stream():
        # One write-session for the whole stream — a StreamingResponse generator
        # runs after the request handler returns, so a Depends/get_db session
        # would already be closed by the time we write here.
        async with AsyncSessionLocal() as session:
            repo = ConversationRepository(session)
            partial_buffer = ""  # accumulates text_delta for the in-flight turn
            last_ai_message: AIMessage | None = None
            last_ai_seq: int | None = None  # row seq of the final assistant turn
            completed_input_tokens = 0
            completed_side_input_tokens = 0
            completed_output_tokens = 0
            # Cost needs the summed usage, not the rate limiter's max-across-
            # iterations figure, so it is accumulated separately for the case
            # where the stream dies before the `done` event carries the totals.
            completed_usage: dict[str, int] | None = None
            completed_secondary_usage: dict[str, int] | None = None
            # Set only on a conversation's first turn; merged into whichever
            # settle path runs so neither can drop it.
            title_usage: dict[str, int] | None = route_usage
            usage_settled = False

            with trace_span(
                "chat_turn",
                **langfuse_identity(
                    conversation_id=conversation_id,
                    enterprise_id=enterprise_id,
                    user_id=user_id,
                    is_internal_user=owner_is_internal_user,
                    internal_user_id=owner_internal_user_id,
                    agent_slug=SPEC.slug,
                ),
            ) as span:
                try:
                    # Create-or-attach the conversation and persist the user's message
                    # immediately, so a stopped turn always leaves a resumable history.
                    if is_new_conversation:
                        conversation = await repo.create(
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                            conversation_id=uuid.UUID(conversation_id),
                            internal_user_id=owner_internal_user_id,
                            is_internal_user=owner_is_internal_user,
                            agent_slug=SPEC.slug,
                        )
                    else:
                        conversation = await repo.get_owned(
                            uuid.UUID(conversation_id),
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                            internal_user_id=owner_internal_user_id,
                            is_internal_user=owner_is_internal_user,
                        )
                        if conversation is None:
                            raise NotFoundError("Conversation not found")

                    seq = await repo.next_seq(conversation.id)
                    human_row = await repo.append_message(
                        conversation,
                        seq=seq,
                        message=human_message,
                    )
                    seq += 1
                    await session.commit()

                    if is_new_conversation:
                        # Titling is a billed Haiku call. Its usage is carried to
                        # settlement below so it lands in the ledger like any other
                        # spend, rather than being charged by Anthropic and recorded
                        # nowhere.
                        title, generated_usage = await generate_conversation_title(
                            user_message=human_message.content,
                            conversation_id=conversation_id,
                            agent_slug=SPEC.slug,
                        )
                        title_usage = merge_usage(title_usage, generated_usage)
                        if title:
                            conversation.title = title
                            await session.commit()
                            logger.info(
                                "Set conversation title conversation_id=%s title=%r",
                                conversation_id,
                                title,
                            )

                    async for event_type, data in runner.handle_message_stream(
                        messages=messages,
                        system_prompt=system_prompt,
                        context_note=context_note,
                        auth=ClickpostAuth(token=token),
                        conversation_id=conversation_id,
                    ):
                        if event_type == "message_complete":
                            # Finalized, balanced message — persist it; never forward
                            # the BaseMessage object to the browser.
                            message = data["message"]
                            _stamp_message_time(message)
                            await repo.append_message(conversation, seq=seq, message=message)
                            if isinstance(message, AIMessage):
                                last_ai_message = message
                                last_ai_seq = seq  # anchor feedback to this row's seq
                                if message.usage_metadata:
                                    completed_input_tokens = max(
                                        completed_input_tokens,
                                        message.usage_metadata.get("input_tokens") or 0,
                                    )
                                    completed_output_tokens += (
                                        message.usage_metadata.get("output_tokens") or 0
                                    )
                                    completed_usage = merge_usage(
                                        completed_usage,
                                        serialize_usage(message.usage_metadata),
                                    )
                            else:
                                hidden_usage = message.additional_kwargs.get(
                                    "usage_metadata"
                                )
                                if isinstance(hidden_usage, dict):
                                    completed_side_input_tokens += (
                                        hidden_usage.get("input_tokens") or 0
                                    )
                                    completed_output_tokens += (
                                        hidden_usage.get("output_tokens") or 0
                                    )
                                    completed_secondary_usage = merge_usage(
                                        completed_secondary_usage, hidden_usage
                                    )
                            seq += 1
                            await session.commit()
                            partial_buffer = ""  # this turn finalized cleanly
                            continue

                        if event_type == "text_delta":
                            partial_buffer += data.get("delta", "")

                        if event_type == "error":
                            mark_current_span_error(data.get("code") or "error")

                        if event_type == "done":
                            usage = data.get("usage") or {}
                            secondary_usage = data.get("secondary_usage") or {}
                            by_iteration = data.get("usage_by_iteration") or []
                            # Each iteration resends the previous one's prompt, so
                            # the largest prompt already covers them all; summing
                            # would bill the same context once per iteration.
                            settled_input = max(
                                (i.get("input_tokens") or 0 for i in by_iteration),
                                default=usage.get("input_tokens") or 0,
                            )
                            settled_output = (usage.get("output_tokens") or 0) + (
                                secondary_usage.get("output_tokens") or 0
                            )
                            await _settle_turn(
                                user_id=user_id,
                                enterprise_id=enterprise_id,
                                is_internal_user=owner_is_internal_user,
                                internal_user_id=owner_internal_user_id,
                                conversation_id=uuid.UUID(conversation_id),
                                human_message_id=human_row.id,
                                input_tokens=settled_input,
                                output_tokens=settled_output,
                                # Rate limiter deliberately excludes the titling
                                # call: it is spend, but it is not something the
                                # user asked for, and charging it to their 24h
                                # window would shorten every new conversation.
                                side_input_tokens=secondary_usage.get("input_tokens") or 0,
                                usage=usage,
                                secondary_usage=merge_usage(secondary_usage, title_usage),
                                budget_state=budget_state,
                                model=SPEC.runtime.model,
                                secondary_model=SPEC.runtime.secondary_model,
                                agent_slug=SPEC.slug,
                            )
                            usage_settled = True
                            thinking = (
                                _extract_thinking(last_ai_message.content)
                                if last_ai_message is not None
                                else None
                            )
                            data = {
                                "conversation_id": conversation_id,
                                "agent": SPEC.slug,
                                "usage": data.get("usage"),
                                "usage_by_iteration": data.get("usage_by_iteration"),
                                "reasoning": thinking,
                                # The final assistant message's own row seq — matches the
                                # seq replayed via GET /conversations/{id}, so feedback
                                # keyed by (conversation_id, seq) lines up either way.
                                "seq": last_ai_seq,
                            }
                            logger.info(
                                "Request cycle done conversation_id=%s user_id=%s "
                                "enterprise_id=%s messages=%d usage=%s scratchpad_usage=%s "
                                "billed_input=%d (max of %d iterations, not the sum)",
                                conversation_id,
                                user_id,
                                enterprise_id,
                                seq,
                                data.get("usage"),
                                # Haiku/scratchpad tokens are billed separately and are often
                                # comparable to the main model's — without this they are invisible.
                                secondary_usage,
                                settled_input,
                                len(by_iteration),
                            )

                        yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

                    # NOTE: titling used to run here, after the stream, which is
                    # what "never delays the response" meant. It now runs *before*
                    # the stream (see is_new_conversation above), so a new
                    # conversation waits a full Haiku round-trip before its first
                    # token. Moving it back would take it past settlement, so its
                    # usage would need its own ledger row rather than joining the
                    # turn's - see docs/budget-tasks.md G8.

                except asyncio.CancelledError:
                    # Client stopped / disconnected mid-stream. Persist whatever
                    # assistant text streamed so far as a display-only partial row
                    # (excluded from replay) so it survives a refresh, then re-raise.
                    # Use a fresh session shielded from the in-flight cancellation:
                    # the streaming session above is already unwinding, and the
                    # commit must not be interrupted.
                    text = partial_buffer.strip()
                    if text:
                        await asyncio.shield(
                            _persist_partial(
                                uuid.UUID(conversation_id),
                                user_id=user_id,
                                enterprise_id=enterprise_id,
                                internal_user_id=owner_internal_user_id,
                                is_internal_user=owner_is_internal_user,
                                text=text,
                            )
                        )
                    raise
                except Exception as e:
                    logger.exception("SSE stream error")
                    if span:
                        span.update(level="ERROR", status_message=str(e))
                    mark_current_span_error(str(e), e)
                    await asyncio.to_thread(
                        alert_slack, f"chat_turn conversation_id={conversation_id}", e
                    )
                    # Intentional AppErrors (e.g. NotFoundError) already carry a
                    # safe, user-facing message; anything else is an unexpected
                    # failure, so don't leak its raw text (SQL, stack internals) to
                    # the browser.
                    user_message = (
                        e.message
                        if isinstance(e, AppError)
                        else "Something went wrong. Please try again."
                    )
                    payload = json.dumps({"code": "internal_error", "message": user_message})
                    yield f"event: error\ndata: {payload}\n\n"
                finally:
                    # The turn ended without a `done` event - cancelled, or it
                    # raised. Settle whatever completed so the hold is released
                    # either way; a hold nobody releases sits on the cap until
                    # the month rolls over.
                    if not usage_settled:
                        if (
                            completed_input_tokens > 0
                            or completed_side_input_tokens > 0
                            or completed_output_tokens > 0
                        ):
                            await asyncio.shield(
                                _settle_turn(
                                    user_id=user_id,
                                    enterprise_id=enterprise_id,
                                    is_internal_user=owner_is_internal_user,
                                    internal_user_id=owner_internal_user_id,
                                    conversation_id=uuid.UUID(conversation_id),
                                    human_message_id=human_row.id,
                                    input_tokens=completed_input_tokens,
                                    output_tokens=completed_output_tokens,
                                    side_input_tokens=completed_side_input_tokens,
                                    usage=completed_usage,
                                    secondary_usage=merge_usage(
                                        completed_secondary_usage, title_usage
                                    ),
                                    budget_state=budget_state,
                                    model=SPEC.runtime.model,
                                    secondary_model=SPEC.runtime.secondary_model,
                                    agent_slug=SPEC.slug,
                                )
                            )
                        else:
                            await asyncio.shield(
                                _release_budget_hold(
                                    enterprise_id=enterprise_id,
                                    agent_slug=SPEC.slug,
                                    budget_state=budget_state,
                                )
                            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
