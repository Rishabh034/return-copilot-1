from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.db import AsyncSessionLocal
from app.dependencies.auth import require_auth
from app.exceptions import NotFoundError
from app.rr_log_analyst.agent import AgentRunner, build_system_prompt
from app.rr_log_analyst.auth import (
    resolve_enterprise_id,
    resolve_token,
    resolve_user_id,
)
from app.rr_log_analyst.memory.store import _extract_thinking, utc_now_iso_ms
from app.rr_log_analyst.repositories import ConversationRepository
from app.rr_log_analyst.repositories.message_mapper import build_partial_ai_message
from app.rr_log_analyst.schemas.messages import SendMessageRequest
from app.rr_log_analyst.services.title import generate_conversation_title
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


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
                conversation_id, user_id=user_id, enterprise_id=enterprise_id
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
    """Returns (user_id, enterprise_id, cp_token) for this request.

    switches on settings.agent_jwt_enabled:
    - True:  identity comes from a verified clickpost-minted JWT
    - False: for local testing 
    """
    if settings.agent_jwt_enabled:
        ctx = await require_auth(authorization=authorization)
        return ctx.user_id, ctx.enterprise_id, ctx.cp_token

    user_id = resolve_user_id(body.user_id)
    enterprise_id = resolve_enterprise_id(body.enterprise_id)
    token = resolve_token(authorization=authorization)
    return user_id, enterprise_id, token


@router.post("/messages/")
async def send_message(
    body: SendMessageRequest,
    request: Request,
    identity: tuple[int, int, str] = Depends(_resolve_identity),
):
    user_id, enterprise_id, token = identity
    clickpost_client = getattr(request.app.state, "clickpost_client", None)
    enterprise_name = body.enterprise_name if body.enterprise_name else None
    username = body.username if body.username else None
    system_prompt = await build_system_prompt(
        clickpost_client,
        token=token,
        enterprise_id=enterprise_id,
        message_time=body.message_time,
        enterprise_name = enterprise_name,
        username = username,
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
            )
            if conversation is None:
                raise NotFoundError("Conversation not found")
            conversation_id = str(conversation.id)
            messages = await repo.load_messages(conversation.id)
        else:
            conversation_id = str(uuid.uuid4())
            messages = []

    human_message = HumanMessage(
        content=body.message,
        additional_kwargs={"message_time": body.message_time or utc_now_iso_ms()},
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

            # Create-or-attach the conversation and persist the user's message
            # immediately, so a stopped turn always leaves a resumable history.
            if is_new_conversation:
                conversation = await repo.create(
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    conversation_id=uuid.UUID(conversation_id),
                )
            else:
                conversation = await repo.get_owned(
                    uuid.UUID(conversation_id),
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                )
                if conversation is None:
                    raise NotFoundError("Conversation not found")

            seq = await repo.next_seq(conversation.id)
            await repo.append_message(conversation, seq=seq, message=human_message)
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

            try:
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
                        seq += 1
                        await session.commit()
                        partial_buffer = ""  # this turn finalized cleanly
                        continue

                    if event_type == "text_delta":
                        partial_buffer += data.get("delta", "")

                    if event_type == "done":
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
                            "enterprise_id=%s messages=%d usage=%s",
                            conversation_id,
                            user_id,
                            enterprise_id,
                            seq,
                            data.get("usage"),
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
                            text=text,
                        )
                    )
                raise
            except Exception as e:
                logger.exception("SSE stream error")
                payload = json.dumps({"code": "internal_error", "message": str(e)})
                yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
