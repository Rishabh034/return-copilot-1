"""The contract an agent implements. Everything else about it is its own business.

An agent is a directory: a prompt, a knowledge base, a tool set, and this spec
pointing at them. The platform supplies conversations, streaming, quotas,
budget, usage, auth and admin, and never needs to know which agent it is
serving.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.tools import BaseTool

from app.config import settings
from app.core.runtime.filters import NULL_FILTER, OutputFilter
from app.core.runtime.policy import ToolGroupPolicy
from app.core.runtime.presentation import DEFAULT_PRESENTATION, Presentation
from app.core.tool_context import ToolContext


@dataclass(frozen=True)
class RuntimeSettings:
    """Model choice and limits, per agent.

    These used to be read straight off the global `rr_analyst_*` settings inside
    `create_chat_model`, which meant an agent could not change its temperature
    or output budget even if it passed a different model id. A cheap agent can
    now run Haiku end to end — primary *and* secondary — by setting two fields,
    with no config change and no effect on anyone else.

    `from_settings()` reproduces today's values exactly, so the allocation agent
    is unchanged and the wire snapshot proves it.
    """

    model: str
    temperature: float
    max_output_tokens: int
    max_iterations: int
    tool_timeout_seconds: int
    thinking_enabled: bool
    thinking_budget_tokens: int
    scratchpad_enabled: bool
    secondary_model: str
    secondary_temperature: float
    secondary_max_output_tokens: int

    @classmethod
    def from_settings(cls, **overrides: Any) -> "RuntimeSettings":
        base = dict(
            model=settings.rr_analyst_model,
            temperature=settings.rr_analyst_temperature,
            max_output_tokens=settings.rr_analyst_max_output_tokens,
            max_iterations=settings.rr_analyst_max_iterations,
            tool_timeout_seconds=settings.rr_analyst_tool_timeout,
            thinking_enabled=settings.rr_analyst_thinking_enabled,
            thinking_budget_tokens=settings.rr_analyst_thinking_budget_tokens,
            scratchpad_enabled=settings.rr_analyst_scratchpad_enabled,
            secondary_model=settings.rr_analyst_secondary_model,
            secondary_temperature=settings.rr_analyst_secondary_temperature,
            secondary_max_output_tokens=settings.rr_analyst_secondary_max_output_tokens,
        )
        base.update(overrides)
        return cls(**base)


@dataclass(frozen=True)
class AgentSpec:
    slug: str
    """Internal id. Stored on the conversation row; never appears in a URL."""

    url_prefix: str
    """Mount point under /api/. Separate from `slug` only so the allocation
    agent can be called `allocation` internally while keeping its existing
    /api/rr-log-analyst/ URL. New agents set both the same."""

    display_name: str

    build_tools: Callable[[ToolContext], list[BaseTool]]
    build_system_prompt: Callable[[], str]
    """The cached half of the prompt. Takes no arguments by design: it carries
    the prompt-cache breakpoint, and the cache matches on exact bytes, so
    anything request-specific here would give every caller its own entry."""

    build_context_note: Callable[..., str]
    """The volatile half — who is asking, and when. Sent after the breakpoint."""

    runtime: RuntimeSettings
    presentation: Presentation = DEFAULT_PRESENTATION

    tool_groups: dict[str, ToolGroupPolicy] = field(default_factory=dict)
    """Mutual-exclusion families across this agent's tools. The platform
    enforces the budget; the agent decides which member wins."""

    output_filter: OutputFilter = NULL_FILTER
    """Applied to assistant text at both the stream and persistence
    boundaries. An agent handling personal data supplies one here rather
    than reaching into the loop."""
    clients: dict[str, Callable[[], Any]] = field(default_factory=dict)
    enabled: bool = True
