from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.clients.clickpost import ClickpostAuth


@dataclass
class ToolContext:
    """Per-request context injected into every LangChain tool invocation."""

    auth: ClickpostAuth
    clickpost_client: Any
    enterprise_user_profile_id: int | None = None
    enterprise_name: str | None = None
    conversation_id: str | None = None
    # Memoized raw Clickpost payloads, keyed by fetch shape. Deliberately scoped to
    # the ToolContext — the runner builds a fresh one per user message — so repeated
    # lookups within one agent loop are free while a user who edits a rule between
    # turns still gets fresh data on the next message.
    raw_fetch_cache: dict[tuple, Any] = field(default_factory=dict)
