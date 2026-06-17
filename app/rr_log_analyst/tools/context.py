from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolContext:
    """Per-request context injected into every LangChain tool invocation."""

    token: str
    clickpost_client: Any
    enterprise_user_profile_id: int | None = None
    enterprise_name: str | None = None
    conversation_id: str | None = None
