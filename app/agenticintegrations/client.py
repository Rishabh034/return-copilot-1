"""
Thin wrapper around the Anthropic client.

Centralizes: model config, retries/backoff, streaming, and basic error handling
so the rest of the codebase never talks to `anthropic` directly.
"""

from __future__ import annotations

import time
import logging
from typing import Any

from anthropic import Anthropic, APIStatusError, APIConnectionError, RateLimitError

logger = logging.getLogger(__name__)


class ClaudeClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
        max_retries: int = 5,
        base_delay: float = 1.0,
    ) -> None:
        # api_key=None lets the SDK fall back to ANTHROPIC_API_KEY env var
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.base_delay = base_delay

    def create_message(
        self,
        messages: list[dict],
        system: str | list[dict] | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        **kwargs: Any,
    ):
        """Single non-streaming call with exponential backoff on transient errors."""
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system is not None:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        payload.update(kwargs)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self.client.messages.create(**payload)
            except RateLimitError as e:
                last_exc = e
                delay = self.base_delay * (2 ** attempt)
                logger.warning("Rate limited, retrying in %.1fs (attempt %d/%d)",
                                delay, attempt + 1, self.max_retries)
                time.sleep(delay)
            except APIConnectionError as e:
                last_exc = e
                delay = self.base_delay * (2 ** attempt)
                logger.warning("Connection error, retrying in %.1fs", delay)
                time.sleep(delay)
            except APIStatusError as e:
                # 4xx that isn't rate limiting is not retryable (bad request, auth, etc.)
                if e.status_code and e.status_code < 500:
                    raise
                last_exc = e
                delay = self.base_delay * (2 ** attempt)
                logger.warning("Server error %s, retrying in %.1fs", e.status_code, delay)
                time.sleep(delay)

        raise RuntimeError(f"Exceeded max retries calling Claude API") from last_exc


