from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies.auth import resolve_identity
from app.exceptions import NotFoundError
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


async def _resolve_identity_body(
    body: ConversationCreateRequest,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str]:
    return await resolve_identity(
        user_id=body.user_id,
        enterprise_id=body.enterprise_id,
        authorization=authorization,
    )


async def _resolve_identity_query(
    user_id: int,
    enterprise_id: int,
    authorization: str | None = Header(default=None),
) -> tuple[int, int, str]:
    return await resolve_identity(
        user_id=user_id,
        enterprise_id=enterprise_id,
        authorization=authorization,
    )


@router.post("/", response_model=ConversationCreateResponse)
async def create_conversation(
    body: ConversationCreateRequest,
    identity: tuple[int, int, str] = Depends(_resolve_identity_body),
    session: AsyncSession = Depends(get_db),
):
    user_id, enterprise_id, _ = identity
    repo = ConversationRepository(session)
    conversation = await repo.create(user_id=user_id, enterprise_id=enterprise_id)
    await session.commit()
    return ConversationCreateResponse(
        id=str(conversation.id),
        created_at=conversation.created_at,
    )


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    session: AsyncSession = Depends(get_db),
):
    user_id, enterprise_id, _ = identity
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
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    session: AsyncSession = Depends(get_db),
):
    user_id, enterprise_id, _ = identity
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
