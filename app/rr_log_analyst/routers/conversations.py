from __future__ import annotations

from fastapi import APIRouter, Header

from app.exceptions import NotFoundError
from app.rr_log_analyst.auth import resolve_token
from app.rr_log_analyst.memory import conversation_store, to_display_messages
from app.rr_log_analyst.schemas.conversations import (
    ConversationCreateResponse,
    ConversationDetail,
    ConversationSummary,
    DisplayMessage,
)

router = APIRouter()


@router.post("/", response_model=ConversationCreateResponse)
async def create_conversation(authorization: str | None = Header(default=None)):
    token = resolve_token(authorization=authorization)
    conversation = conversation_store.create(token)
    return ConversationCreateResponse(
        id=conversation.id,
        created_at=conversation.created_at,
    )


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(authorization: str | None = Header(default=None)):
    token = resolve_token(authorization=authorization)
    conversations = conversation_store.list_by_token(token)
    summaries: list[ConversationSummary] = []

    for conversation in conversations:
        display_messages = to_display_messages(conversation.messages)
        preview = display_messages[0]["content"][:120] if display_messages else None
        summaries.append(
            ConversationSummary(
                id=conversation.id,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                message_count=len(display_messages),
                preview=preview,
            )
        )

    return summaries


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    authorization: str | None = Header(default=None),
):
    token = resolve_token(authorization=authorization)
    conversation = conversation_store.get(conversation_id)
    if conversation is None or conversation.token != token:
        raise NotFoundError("Conversation not found")

    display_messages = [
        DisplayMessage(**message)
        for message in to_display_messages(conversation.messages)
    ]
    return ConversationDetail(
        id=conversation.id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=display_messages,
    )
