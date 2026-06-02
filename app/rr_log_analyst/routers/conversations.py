from __future__ import annotations

import uuid as _uuid
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from app.database import get_db
from app.config import settings
from app.exceptions import NotFoundError
from app.rr_log_analyst.schemas.conversations import (
    ConversationSummary,
    CreateConversationResponse,
    MessageOut,
)
from app.rr_log_analyst.services.system_prompt import build_system_prompt

router = APIRouter()

HARDCODED_ENTERPRISE_ID = 1


@router.post("/", response_model=CreateConversationResponse, status_code=201)
async def create_conversation(
    db: asyncpg.Connection = Depends(get_db),
):
    enterprise_id = HARDCODED_ENTERPRISE_ID  # TODO: from auth dependency
    conv_id = str(_uuid.uuid4())
    system_prompt = build_system_prompt()
    await db.execute(
        """
        INSERT INTO rr_log_analyst_conversation (id, enterprise_id, system_prompt, model)
        VALUES ($1, $2, $3, $4)
        """,
        conv_id,
        enterprise_id,
        system_prompt,
        settings.rr_analyst_model,
    )
    return CreateConversationResponse(conversation_id=conv_id)


@router.get("/", response_model=list[ConversationSummary])
async def list_conversations(
    db: asyncpg.Connection = Depends(get_db),
):
    enterprise_id = HARDCODED_ENTERPRISE_ID  # TODO: from auth dependency
    rows = await db.fetch(
        """
        SELECT id, title, model, created_at, updated_at
        FROM rr_log_analyst_conversation
        WHERE enterprise_id = $1
        ORDER BY updated_at DESC
        LIMIT 50
        """,
        enterprise_id,
    )
    return [ConversationSummary(**dict(r)) for r in rows]


@router.get("/{conversation_id}/history", response_model=list[MessageOut])
async def get_conversation_history(
    conversation_id: UUID,
    db: asyncpg.Connection = Depends(get_db),
):
    enterprise_id = HARDCODED_ENTERPRISE_ID  # TODO: from auth dependency
    conv = await db.fetchrow(
        "SELECT id FROM rr_log_analyst_conversation WHERE id = $1 AND enterprise_id = $2",
        str(conversation_id),
        enterprise_id,
    )
    if not conv:
        raise NotFoundError("Conversation not found")

    rows = await db.fetch(
        """
        SELECT id, seq, role, content, tool_name, tool_args, tool_result, created_at
        FROM rr_log_analyst_message
        WHERE conversation_id = $1
        ORDER BY seq ASC
        """,
        str(conversation_id),
    )
    return [MessageOut(**dict(r)) for r in rows]
