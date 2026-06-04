from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]: ...


class ToolContext:
    """Passed to every tool — carries the Clickpost auth token + shared clients.

    `token` is supplied per request by the caller (the dashboard sends it as an
    `Authorization: Token <token>` header, which the agent API forwards here and
    on to Clickpost). It both authenticates the Clickpost call and scopes it to
    the right enterprise — no separate enterprise_id is needed.
    """

    def __init__(self, token: str, clickpost_client: Any):
        self.token = token
        self.clickpost_client = clickpost_client


_registry: dict[str, Tool] = {}


def register(tool_cls: type) -> type:
    instance = tool_cls()
    _registry[instance.name] = instance
    return tool_cls


def get_all_tools() -> list[Tool]:
    return list(_registry.values())


def get_tool(name: str) -> Tool | None:
    return _registry.get(name)


def get_claude_tool_schemas() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in _registry.values()
    ]


# Import tool modules so their @register decorators run on package import.
# Kept at the bottom to avoid a circular import (the modules import ToolContext
# and register from this package, which are defined above).
from app.rr_log_analyst.tools import accounts, rr_logs, rules  # noqa: E402,F401
