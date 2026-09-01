from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from app.clients.clickpost import ClickpostAuth
from app.exceptions import AppError, BadRequestError
from app.rr_log_analyst.agent import AgentRunner, build_context_note, build_system_prompt
from app.rr_log_analyst.memory.tool_results import tool_result_store
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class IntegrationQueryRequest(BaseModel):
    key: str = Field(..., description="Clickpost enterprise license key (unique_id UUID)")
    message: str = Field(..., min_length=1, max_length=10_000)
    message_time: str | None = None
    stream: bool = False


class IntegrationQueryResponse(BaseModel):
    answer: str
    reasoning: str | None
    tools_used: list[str]
    usage: dict | None


def _auth(key: str) -> ClickpostAuth:
    try:
        uuid.UUID(key)
    except ValueError:
        raise BadRequestError("Invalid license key")
    return ClickpostAuth(key=key)


@router.post("/query", response_model=None)
async def query(body: IntegrationQueryRequest, request: Request):
    """Single-shot agent run for service-to-service callers.

    Authenticates downstream with the caller's enterprise license key rather
    than a dashboard token — Clickpost's v4/v5 profile decorators accept
    `?key=<unique_id>` in place of an Authorization header. Stateless: nothing
    is persisted, so there is no conversation to continue.

    Takes no enterprise id or name. The key already scopes every downstream
    lookup to one enterprise, so those fields only ever reached the prompt as
    caller-asserted text — a caller could name an enterprise whose data the key
    does not grant, and the model would repeat it back.

    Deliberately carries no dashboard token or agent-JWT dependency, and must
    keep it that way: callers are other services, which have a license key but
    no user identity, so resolve_identity has nothing to resolve here. Access is
    gated at the network perimeter (VPS allowlist) instead. Do not add
    resolve_identity to this route for consistency with the /messages/,
    /conversations/ and /feedback/ routers — it would lock out every integration
    caller the moment AGENT_JWT_ENABLED is turned on.
    """
    auth = _auth(body.key)
    runner = AgentRunner(clickpost_client=request.app.state.clickpost_client)
    conversation_id = str(uuid.uuid4())

    events = runner.handle_message_stream(
        messages=[HumanMessage(content=body.message.strip())],
        system_prompt=build_system_prompt(),
        context_note=build_context_note(message_time=body.message_time),
        model=settings.rr_analyst_model,
        auth=auth,
        conversation_id=conversation_id,
    )

    if body.stream:
        return StreamingResponse(
            _event_stream(events, conversation_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return await _collect(events, conversation_id)


async def _event_stream(events, conversation_id: str):
    try:
        async for event_type, data in events:
            # message_complete carries BaseMessage objects, which only the
            # persisting path needs and which are not JSON-serializable.
            if event_type == "message_complete":
                continue
            if event_type == "done":
                data = {**data, "conversation_id": conversation_id}
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    finally:
        tool_result_store.purge_conversation(conversation_id)


async def _collect(events, conversation_id: str) -> IntegrationQueryResponse:
    answer = ""
    buffer = ""
    reasoning = ""
    tools_used: list[str] = []
    usage: dict | None = None
    error: dict | None = None

    try:
        async for event_type, data in events:
            if event_type == "text_delta":
                buffer += data.get("delta", "")
            elif event_type == "thinking_delta":
                reasoning += data.get("delta", "")
            elif event_type == "tool_start":
                tools_used.append(data["tool"])
            elif event_type == "message_complete":
                # An assistant turn just closed: whatever streamed since the
                # last one is that turn's text. Only the last turn is the answer
                # — earlier ones are the agent talking between tool calls.
                if isinstance(data["message"], AIMessage):
                    answer = buffer
                buffer = ""
            elif event_type == "done":
                usage = data.get("usage")
            elif event_type == "error":
                error = data
    finally:
        tool_result_store.purge_conversation(conversation_id)

    if error is not None:
        logger.error("Integration query failed: %s", error)
        raise AppError(error.get("message") or "The assistant could not complete the request.")

    return IntegrationQueryResponse(
        answer=answer,
        reasoning=reasoning or None,
        tools_used=tools_used,
        usage=usage,
    )
