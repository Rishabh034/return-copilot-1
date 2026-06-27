from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.db import AsyncSessionLocal
from app.exceptions import NotFoundError
from app.rr_log_analyst.agent import AgentRunner, build_system_prompt
from app.rr_log_analyst.auth import (
    resolve_enterprise_id,
    resolve_token,
    resolve_user_id,
)
from app.rr_log_analyst.memory import messages_storage_stats, tool_result_store
from app.rr_log_analyst.memory.store import _extract_thinking, utc_now_iso_ms
from app.rr_log_analyst.repositories import ConversationRepository
from app.rr_log_analyst.schemas.messages import SendMessageRequest
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _stamp_missing_message_time(messages: list[BaseMessage]) -> None:
    """Ensure each user/assistant message carries a display message_time."""
    for message in messages:
        if not isinstance(message, (HumanMessage, AIMessage)):
            continue
        if not message.additional_kwargs.get("message_time"):
            message.additional_kwargs["message_time"] = utc_now_iso_ms()


@router.post("/messages/")
async def send_message(
    body: SendMessageRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user_id = resolve_user_id(body.user_id)
    enterprise_id = resolve_enterprise_id(body.enterprise_id)
    token = resolve_token(authorization=authorization)
    clickpost_client = getattr(request.app.state, "clickpost_client", None)
    system_prompt = await build_system_prompt(
        clickpost_client,
        token=token,
        enterprise_id=enterprise_id,
        message_time=body.message_time,
    )
    runner = AgentRunner(clickpost_client=clickpost_client)

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
            # Allocate an id but don't persist yet — a turn that errors out before
            # completing must not leave an empty conversation in the store.
            conversation_id = str(uuid.uuid4())
            messages = []

    messages.append(
        HumanMessage(
            content=body.message,
            additional_kwargs={"message_time": body.message_time or utc_now_iso_ms()},
        )
    )
    request_stats = messages_storage_stats(messages)
    is_new_conversation = body.conversation_id is None

    async def event_stream():
        try:
            async for event_type, data in runner.handle_message_stream(
                messages=messages,
                system_prompt=system_prompt,
                model=settings.rr_analyst_model,
                token=token,
                conversation_id=conversation_id,
            ):
                if event_type == "done":
                    updated_messages = data.pop("messages", messages)
                    thinking = (
                        _extract_thinking(updated_messages[-1].content)
                        if updated_messages
                        else None
                    )
                    _stamp_missing_message_time(updated_messages)

                    async with AsyncSessionLocal() as session:
                        repo = ConversationRepository(session)
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
                        await repo.save_messages(conversation, updated_messages)
                        response_stats = messages_storage_stats(updated_messages)
                        store_stats = await repo.storage_stats()
                        user_stats = await repo.storage_stats_for_user(
                            user_id=user_id,
                            enterprise_id=enterprise_id,
                        )
                        await session.commit()

                    logger.info(
                        "Request cycle storage conversation_id=%s user_id=%s enterprise_id=%s "
                        "req_messages=%d req_bytes=%d req_roles=%s "
                        "resp_messages=%d resp_bytes=%d resp_roles=%s delta_bytes=%+d "
                        "store_conversations=%d store_messages=%d store_bytes=%d "
                        "user_conversations=%d user_messages=%d user_bytes=%d "
                        "usage=%s",
                        body.enterprise_id,
                        conversation_id,
                        user_id,
                        enterprise_id,
                        request_stats["message_count"],
                        request_stats["storage_bytes"],
                        request_stats["messages_by_role"],
                        response_stats["message_count"],
                        response_stats["storage_bytes"],
                        response_stats["messages_by_role"],
                        response_stats["storage_bytes"] - request_stats["storage_bytes"],
                        store_stats["conversation_count"],
                        store_stats["message_count"],
                        store_stats["storage_bytes"],
                        user_stats["conversation_count"],
                        user_stats["message_count"],
                        user_stats["storage_bytes"],
                        data.get("usage"),
                    )
                    data = {
                        "conversation_id": conversation_id,
                        "usage": data.get("usage"),
                        "usage_by_iteration": data.get("usage_by_iteration"),
                        "reasoning": thinking,
                    }
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        except Exception as e:
            logger.exception("SSE stream error")
            async with AsyncSessionLocal() as session:
                repo = ConversationRepository(session)
                if not await repo.exists(uuid.UUID(conversation_id)):
                    tool_result_store.purge_conversation(conversation_id)
            payload = json.dumps({"code": "internal_error", "message": str(e)})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
