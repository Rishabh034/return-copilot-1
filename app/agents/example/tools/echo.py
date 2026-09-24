"""One tool, showing the shape every tool module follows.

`build_tools(context)` returns the tools bound to this request's context. The
context carries the caller's identity and the clients the spec asked for, so a
tool never reaches for a global.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.runtime.policy import ToolPolicy, with_policy
from app.core.tool_context import ToolContext


class EchoInput(BaseModel):
    text: str = Field(description="Text to echo back.")


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def echo(text: str) -> dict[str, Any]:
        # Return a dict. A key named `error` marks a failure; anything else is a
        # result the agent's presentation can describe.
        return {"echoed": text, "conversation_id": context.conversation_id}

    return [
        with_policy(
            StructuredTool.from_function(
                coroutine=echo,
                name="echo",
                # The description is what the model routes on, and it is part of
                # the cached prefix. Say when to use the tool, not just what it
                # does.
                description="Echo text back. Use only to demonstrate a tool call.",
                args_schema=EchoInput,
            ),
            # Declared for illustration; ToolPolicy() is the default and can be
            # omitted entirely. `reusable=False` would force a re-run on an
            # identical call, which is what a simulation needs.
            ToolPolicy(reusable=True, scratchpad=True),
        )
    ]
