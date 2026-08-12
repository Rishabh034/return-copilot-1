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
from app.dependencies.auth import resolve_identity
from app.clients.observability import trace_span
from app.exceptions import AppError, NotFoundError, BadRequestError
from app.utilities.alerts import alert_slack
from app.rr_log_analyst.agent import AgentRunner, build_system_prompt
from app.rr_log_analyst.auth import (
    resolve_internal_user_id,
    resolve_is_internal_user,
)
from app.rr_log_analyst.memory.store import _extract_thinking, utc_now_iso_ms
from app.rr_log_analyst.models.message import Message
from app.rr_log_analyst.repositories import ConversationRepository, UserTokenRepository
from app.rr_log_analyst.repositories.message_mapper import build_partial_ai_message
from app.rr_log_analyst.schemas.messages import SendMessageRequest
from app.rr_log_analyst.services.title import generate_conversation_title
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

async def _settle_user_tokens(
    *,
    user_id: int,
    enterprise_id: int,
    is_internal_user: bool,
    internal_user_id: str | None,
    input_tokens: int,
    output_tokens: int,
    human_message_id: uuid.UUID,
    side_input_tokens: int = 0,
) -> None:
    if input_tokens <= 0 and output_tokens <= 0 and side_input_tokens <= 0:
        return
    async with AsyncSessionLocal() as session:
        repo = UserTokenRepository(session)
        await session.execute(
            update(Message)
            .where(Message.id == human_message_id)
            .values(token_count=input_tokens)
        )
        await repo.update_tokens(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            side_input_tokens=side_input_tokens,
        )
        await session.commit()


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
) -> tuple[int, int, str]:
    return await resolve_identity(
        user_id=body.user_id,
        enterprise_id=body.enterprise_id,
        authorization=authorization,
    
    )


@router.post("/messages/")
async def send_message(
    body: SendMessageRequest,
    request: Request,
    identity: tuple[int, int, str] = Depends(_resolve_identity),
):
    user_id, enterprise_id, token = identity
    is_internal_user = resolve_is_internal_user(body.is_internal_user)
    internal_user_id = resolve_internal_user_id(body.internal_user_id)

    if not is_internal_user:
        internal_user_id = None

    if is_internal_user and internal_user_id is None:
        raise BadRequestError("Internal user ID is required")

    async with AsyncSessionLocal() as session:
        token_repo = UserTokenRepository(session)
        allowed = await token_repo.can_start(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
        )
        quota = await token_repo.get_owned(
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
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
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Token limit reached.",
                "reset_at": reset_at.isoformat() if reset_at else None,
                "can_claim_extra_tokens": can_claim_extra_tokens,
            },
            headers={"Retry-After": str(retry_after)},
        )

    clickpost_client = getattr(request.app.state, "clickpost_client", None)
    enterprise_name = body.enterprise_name if body.enterprise_name else None
    username = body.username if body.username else None
    raw_message = body.message
    display_message = raw_message.strip()

    system_prompt = await build_system_prompt(
        clickpost_client,
        token=token,
        enterprise_id=enterprise_id,
        message_time=body.message_time,
        enterprise_name=enterprise_name,
        username=username,
    )
    runner = AgentRunner(clickpost_client=clickpost_client)
    # Ownership / existence check up front so existing-but-unowned conversations
    # get a real 404 instead of a 200 stream carrying an error event. The replay
    # history loaded here (completed turns only) is what the agent loop sees.
    is_new_conversation = body.conversation_id is None
    async with AsyncSessionLocal() as session:
        repo = ConversationRepository(session)
        if body.conversation_id:
            conversation = await repo.get_owned(
                uuid.UUID(body.conversation_id),
                user_id=user_id,
                enterprise_id=enterprise_id,
                internal_user_id=internal_user_id,
                is_internal_user=is_internal_user,
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
            usage_settled = False

            with trace_span(
                "chat_turn", metadata={"langfuse_session_id": conversation_id}
            ) as span:
                try:
                    # Create-or-attach the conversation and persist the user's message
                    # immediately, so a stopped turn always leaves a resumable history.
                    if is_new_conversation:
                        conversation = await repo.create(
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                            conversation_id=uuid.UUID(conversation_id),
                            internal_user_id=internal_user_id,
                            is_internal_user=is_internal_user,
                        )
                    else:
                        conversation = await repo.get_owned(
                            uuid.UUID(conversation_id),
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                            internal_user_id=internal_user_id,
                            is_internal_user=is_internal_user,
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
                        title = await generate_conversation_title(
                            user_message=human_message.content
                        )
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
                        model=settings.rr_analyst_model,
                        token=token,
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
                            seq += 1
                            await session.commit()
                            partial_buffer = ""  # this turn finalized cleanly
                            continue

                        if event_type == "text_delta":
                            partial_buffer += data.get("delta", "")

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
                        await _settle_user_tokens(
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                            is_internal_user=is_internal_user,
                            internal_user_id=internal_user_id,
                            input_tokens=settled_input,
                            output_tokens=settled_output,
                            side_input_tokens=secondary_usage.get("input_tokens") or 0,
                            human_message_id=human_row.id,
                        )
                        usage_settled = True
                        thinking = (
                            _extract_thinking(last_ai_message.content)
                            if last_ai_message is not None
                            else None
                        )
                        data = {
                            "conversation_id": conversation_id,
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

                    # First exchange on a fresh conversation — title it from the
                    # opening user/assistant turn with a small Haiku call. Runs after
                    # the answer has fully streamed, so it never delays the response;
                    # best-effort, so a failure leaves the conversation untitled.

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
                                internal_user_id=internal_user_id,
                                is_internal_user=is_internal_user,
                                text=text,
                            )
                        )
                    raise
                except Exception as e:
                    logger.exception("SSE stream error")
                    if span:
                        span.update(level="ERROR", status_message=str(e))
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
                    if not usage_settled and (
                        completed_input_tokens > 0
                        or completed_side_input_tokens > 0
                        or completed_output_tokens > 0
                    ):
                        await asyncio.shield(
                            _settle_user_tokens(
                                user_id=user_id,
                                enterprise_id=enterprise_id,
                                is_internal_user=is_internal_user,
                                internal_user_id=internal_user_id,
                                input_tokens=completed_input_tokens,
                                output_tokens=completed_output_tokens,
                                side_input_tokens=completed_side_input_tokens,
                                human_message_id=human_row.id,
                            )
                        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
