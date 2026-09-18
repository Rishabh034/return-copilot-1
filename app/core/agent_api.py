"""One conversational API, mounted once per agent.

The routes a chat agent needs — send a message, list and read conversations,
leave feedback — are identical for every agent. Only the prompt, the tools and
the vocabulary differ, and all three come from the spec.

So there is one implementation, and `build_agent_router` binds a spec to it.
The spec travels on `request.state.agent_spec`, set by a router-level
dependency, which keeps the handlers at module scope and free of any
agent-specific import.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.request_spec import bind_spec
from app.core.routers import common, conversations, feedback, messages
from app.core.spec import AgentSpec


def build_agent_router(spec: AgentSpec) -> APIRouter:
    router = APIRouter(dependencies=[Depends(bind_spec(spec))])
    router.include_router(conversations.router, prefix="/conversations")
    router.include_router(feedback.router, prefix="/feedback")
    router.include_router(messages.router)
    router.include_router(common.router)
    return router
