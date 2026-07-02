from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.exceptions import NotFoundError
from app.rr_log_analyst.auth import (
    resolve_enterprise_id,
    resolve_token,
    resolve_user_id,
)
from app.rr_log_analyst.memory import to_display_messages
from app.rr_log_analyst.repositories import ConversationRepository
from app.rr_log_analyst.repositories.message_mapper import rows_to_messages
from app.rr_log_analyst.schemas.conversations import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDetail,
    ConversationSummary,
    DisplayMessage,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=ConversationCreateResponse)
async def create_conversation(
    body: ConversationCreateRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    resolve_token(authorization=authorization)
    user_id = resolve_user_id(body.user_id)
    enterprise_id = resolve_enterprise_id(body.enterprise_id)
    repo = ConversationRepository(session)
    conversation = await repo.create(user_id=user_id, enterprise_id=enterprise_id)
    await session.commit()
    return ConversationCreateResponse(
        id=str(conversation.id),
        created_at=conversation.created_at,
    )


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    user_id: int,
    enterprise_id: int,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    resolve_token(authorization=authorization)
    user_id = resolve_user_id(user_id)
    enterprise_id = resolve_enterprise_id(enterprise_id)
    repo = ConversationRepository(session)
    conversations = await repo.list_for_user(
        user_id=user_id,
        enterprise_id=enterprise_id,
    )
    logger.info(
        "Listed conversations user_id=%s enterprise_id=%s count=%d",
        user_id,
        enterprise_id,
        len(conversations),
    )
    summaries: list[ConversationSummary] = []

    for conversation in conversations:
        langchain_messages = rows_to_messages(
            list(conversation.messages),
            conversation_id=str(conversation.id),
        )
        display_messages = to_display_messages(langchain_messages)
        preview = display_messages[0]["content"][:120] if display_messages else None
        summaries.append(
            ConversationSummary(
                id=str(conversation.id),
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                message_count=len(display_messages),
                title=conversation.title,
                preview=preview,
            )
        )

    return summaries


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    user_id: int,
    enterprise_id: int,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    resolve_token(authorization=authorization)
    user_id = resolve_user_id(user_id)
    enterprise_id = resolve_enterprise_id(enterprise_id)
    repo = ConversationRepository(session)
    conversation = await repo.get_owned(
        uuid.UUID(conversation_id),
        user_id=user_id,
        enterprise_id=enterprise_id,
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")

    langchain_messages = rows_to_messages(
        list(conversation.messages),
        conversation_id=str(conversation.id),
    )
    display_messages = [
        DisplayMessage(**message)
        for message in to_display_messages(langchain_messages)
    ]
    return ConversationDetail(
        id=str(conversation.id),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
        messages=display_messages,
    )
