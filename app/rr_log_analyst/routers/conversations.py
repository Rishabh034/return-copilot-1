from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies.auth import resolve_identity
from app.exceptions import BadRequestError, NotFoundError
from app.rr_log_analyst.auth import (
    resolve_internal_user_id,
    resolve_is_internal_user,
)

from app.rr_log_analyst.memory import to_display_messages
from app.rr_log_analyst.repositories import ConversationRepository
from app.rr_log_analyst.repositories.message_mapper import (
    rows_to_messages,
    rows_to_transcript,
)
from app.rr_log_analyst.schemas.conversations import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDetail,
    ConversationSummary,
    ConversationTranscript,
    DisplayMessage,
    TranscriptMessage,
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
    internal_user_id = resolve_internal_user_id(body.internal_user_id)
    is_internal_user = resolve_is_internal_user(body.is_internal_user)

    if not is_internal_user:
        internal_user_id = None  # Clear internal_user_id if the user is not internal

    if is_internal_user:
        if internal_user_id is None:
            raise BadRequestError("Internal user ID is required")
        
    repo = ConversationRepository(session)
    
    conversation = await repo.create(user_id=user_id, enterprise_id=enterprise_id, internal_user_id=internal_user_id, is_internal_user=is_internal_user)
    await session.commit()
    return ConversationCreateResponse(
        id=str(conversation.id),
        created_at=conversation.created_at,
    )


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    internal_user_id: str | None = None,
    is_internal_user: bool = False,
    session: AsyncSession = Depends(get_db),
):
    user_id, enterprise_id, _ = identity
    internal_user_id = resolve_internal_user_id(internal_user_id)
    is_internal_user = resolve_is_internal_user(is_internal_user)
    
    if not is_internal_user:
        internal_user_id = None
        
    if is_internal_user:
        if internal_user_id is None:
            raise BadRequestError("Internal user ID is required")
   
    repo = ConversationRepository(session)
    conversations = await repo.list_for_user(
        user_id=user_id,
        enterprise_id=enterprise_id,
        internal_user_id=internal_user_id,
        is_internal_user=is_internal_user,
    )
    logger.info(
        "Listed conversations user_id=%s enterprise_id=%s internal_user_id=%s is_internal_user=%s count=%s",
        user_id,
        enterprise_id,
        internal_user_id,
        is_internal_user,
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


@router.get("/{conversation_id}/transcript", response_model=ConversationTranscript)
async def get_conversation_transcript(
    conversation_id: str,
    include_raw: bool = False,
    is_internal: bool = False,
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    internal_user_id: str | None = None,
    is_internal_user: bool = False,
    session: AsyncSession = Depends(get_db),
):
    user_id, enterprise_id, _ = identity
    repo = ConversationRepository(session)
    user_id, enterprise_id, _ = identity
    nternal_user_id = resolve_internal_user_id(internal_user_id)
    is_internal_user = resolve_is_internal_user(is_internal_user)
    
    if not is_internal_user:
        internal_user_id = None
        
    if is_internal_user:
        if internal_user_id is None:
            raise BadRequestError("Internal user ID is required")
        
    conversation = await repo.get_owned(
        uuid.UUID(conversation_id),
        user_id=user_id,
        enterprise_id=enterprise_id,
        internal_user_id=internal_user_id,
        is_internal_user=is_internal_user,
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")

    entries = rows_to_transcript(
        list(conversation.messages), include_raw=include_raw
    )
    logger.info(
        "Transcript conversation_id=%s user_id=%s enterprise_id=%s "
        "is_internal=%s messages=%d",
        conversation_id,
        user_id,
        enterprise_id,
        is_internal,
        len(entries),
    )
    return ConversationTranscript(
        id=str(conversation.id),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
        is_internal=is_internal,
        messages=[TranscriptMessage(**entry) for entry in entries],
    )


@router.get("/{conversation_id}/transcript", response_model=ConversationTranscript)
async def get_conversation_transcript(
    conversation_id: str,
    include_raw: bool = False,
    is_internal: bool = False,
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    internal_user_id: str | None = None,
    is_internal_user: bool = False,
    session: AsyncSession = Depends(get_db),
):
    """Whole ordered conversation: human, ai (with tool_calls + reasoning), and
    tool result turns, in seq order. Scoped to the caller's own conversation
    (404 for anyone else's). Set include_raw=true to keep the bulky
    raw_data_parts inside persisted tool results. is_internal is echoed back on
    the response for the caller."""
    user_id, enterprise_id, _ = identity
    repo = ConversationRepository(session)
    user_id, enterprise_id, _ = identity
    nternal_user_id = resolve_internal_user_id(internal_user_id)
    is_internal_user = resolve_is_internal_user(is_internal_user)
    
    if not is_internal_user:
        internal_user_id = None
        
    if is_internal_user:
        if internal_user_id is None:
            raise BadRequestError("Internal user ID is required")
    conversation = await repo.get_owned(
        uuid.UUID(conversation_id),
        user_id=user_id,
        enterprise_id=enterprise_id,
        internal_user_id=internal_user_id,
        is_internal_user=is_internal_user,
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")

    entries = rows_to_transcript(
        list(conversation.messages), include_raw=include_raw
    )
    logger.info(
        "Transcript conversation_id=%s user_id=%s enterprise_id=%s "
        "is_internal=%s messages=%d",
        conversation_id,
        user_id,
        enterprise_id,
        is_internal,
        len(entries),
    )
    return ConversationTranscript(
        id=str(conversation.id),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
        is_internal=is_internal,
        messages=[TranscriptMessage(**entry) for entry in entries],
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    identity: tuple[int, int, str] = Depends(_resolve_identity_query),
    internal_user_id: str | None = None,
    is_internal_user: bool = False,
    session: AsyncSession = Depends(get_db),
):
    repo = ConversationRepository(session)
    user_id, enterprise_id, _ = identity
    nternal_user_id = resolve_internal_user_id(internal_user_id)
    is_internal_user = resolve_is_internal_user(is_internal_user)
    
    if not is_internal_user:
        internal_user_id = None
        
    if is_internal_user:
        if internal_user_id is None:
            raise BadRequestError("Internal user ID is required")
    conversation = await repo.get_owned(
        uuid.UUID(conversation_id),
        user_id=user_id,
        enterprise_id=enterprise_id,
        internal_user_id=internal_user_id,
        is_internal_user=is_internal_user,
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
