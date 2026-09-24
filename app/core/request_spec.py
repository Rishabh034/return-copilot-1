"""Which agent is this request for?

Set by the router-level dependency `build_agent_router` installs, and read by
handlers. Lives apart from `agent_api` so the router modules can read it
without importing the module that imports them.
"""

from __future__ import annotations

from fastapi import Request

from app.core.spec import AgentSpec


def bind_spec(spec: AgentSpec):
    async def _dependency(request: Request) -> None:
        request.state.agent_spec = spec

    return _dependency


def agent_spec(request: Request) -> AgentSpec:
    """The spec for the mount this request arrived on."""
    return request.state.agent_spec
