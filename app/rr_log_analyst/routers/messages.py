from __future__ import annotations

import json
import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.database import get_db
from app.exceptions import NotFoundError
from app.rr_log_analyst.routers.conversations import HARDCODED_ENTERPRISE_ID
from app.rr_log_analyst.schemas.messages import SendMessageRequest
from app.rr_log_analyst.services.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{conversation_id}/messages/")
async def send_message(
    conversation_id: UUID,
    body: SendMessageRequest,
    request: Request,
    db: asyncpg.Connection = Depends(get_db),
):
    enterprise_id = HARDCODED_ENTERPRISE_ID  # TODO: from auth dependency
    conv_id_str = str(conversation_id)

    conv = await db.fetchrow(
        "SELECT id, enterprise_id, system_prompt, model "
        "FROM rr_log_analyst_conversation WHERE id = $1 AND enterprise_id = $2",
        conv_id_str,
        enterprise_id,
    )
    if not conv:
        raise NotFoundError("Conversation not found")

    clickpost_client = getattr(request.app.state, "clickpost_client", None)
    orchestrator = Orchestrator(
        db=db,
        clickpost_client=clickpost_client,
        anthropic_client=request.app.state.anthropic_client,
    )

    async def event_stream():
        try:
            async for event_type, data in orchestrator.handle_message_stream(
                conversation_id=conv_id_str,
                user_text=body.message,
                enterprise_id=conv["enterprise_id"],
                system_prompt=conv["system_prompt"],
                model=conv["model"],
            ):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        except Exception as e:
            logger.exception("SSE stream error for conversation %s", conv_id_str)
            payload = json.dumps({"code": "internal_error", "message": str(e)})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
