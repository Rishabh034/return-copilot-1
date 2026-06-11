from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolContext:
    """Per-request context injected into every LangChain tool invocation."""

    token: str
    clickpost_client: Any
