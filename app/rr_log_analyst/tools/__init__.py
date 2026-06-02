from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]: ...


class ToolContext:
    """Passed to every tool — carries enterprise scope + shared clients."""

    def __init__(self, enterprise_id: int, clickpost_client: Any):
        self.enterprise_id = enterprise_id
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
