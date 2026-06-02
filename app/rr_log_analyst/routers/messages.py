from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.rr_log_analyst.schemas.messages import SendMessageRequest
from app.rr_log_analyst.services.orchestrator import Orchestrator
from app.rr_log_analyst.services.system_prompt import build_system_prompt
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/messages/")
async def send_message(
    body: SendMessageRequest,
    request: Request,
):
    system_prompt = build_system_prompt()
    clickpost_client = getattr(request.app.state, "clickpost_client", None)
    orchestrator = Orchestrator(
        clickpost_client=clickpost_client,
        anthropic_client=request.app.state.anthropic_client,
    )

    async def event_stream():
        try:
            async for event_type, data in orchestrator.handle_message_stream(
                user_text=body.message,
                system_prompt=system_prompt,
                model=settings.rr_analyst_model,
            ):
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        except Exception as e:
            logger.exception("SSE stream error")
            payload = json.dumps({"code": "internal_error", "message": str(e)})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
