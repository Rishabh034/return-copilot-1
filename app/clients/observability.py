"""
Wires this app's two LLM call paths up to self-hosted Langfuse for tracing:
LangChain's ChatAnthropic (app/clients/llm.py) via its callback handler, and
the raw Anthropic SDK wrapper (app/agenticintegrations/client.py) via OTEL
auto-instrumentation, which patches the anthropic package globally so no
call-site changes are needed there.

No-ops entirely when `settings.langfuse_enabled` is false, so this is safe to
call unconditionally at startup.
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack, contextmanager

from app.config import settings

logger = logging.getLogger(__name__)

_langchain_handler_cls: type | None = None


def init_langfuse() -> None:
    """Call once at process startup, before any Anthropic client is used."""
    if not settings.langfuse_enabled:
        return
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning(
            "langfuse_enabled is true but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY "
            "is missing; skipping tracing setup"
        )
        return

    # The langfuse SDK and OTEL instrumentor read these from os.environ
    # directly, regardless of whether our own settings came from AWS
    # Secrets Manager or a local .env file.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_base_url)

    from langfuse import get_client
    from langfuse.langchain import CallbackHandler
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    global _langchain_handler_cls
    _langchain_handler_cls = CallbackHandler

    client = get_client()
    AnthropicInstrumentor().instrument()

    try:
        if client.auth_check():
            logger.info("Langfuse tracing enabled (host=%s)", settings.langfuse_base_url)
        else:
            logger.error(
                "Langfuse auth_check failed — check LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY "
                "against host=%s",
                settings.langfuse_base_url,
            )
    except Exception:
        logger.exception(
            "Langfuse auth_check errored — host=%s may be unreachable from this process",
            settings.langfuse_base_url,
        )


def shutdown_langfuse() -> None:
    """Flush buffered traces before the process exits."""
    if _langchain_handler_cls is None:
        return
    from langfuse import get_client

    get_client().flush()


def langchain_trace_config(session_id: str | None = None) -> dict:
    """Build a LangChain `config=` dict that routes a call through Langfuse.

    Returns {} when tracing is disabled, so callers can pass this through
    unconditionally without branching on settings.langfuse_enabled themselves.
    """
    if _langchain_handler_cls is None:
        return {}
    config: dict = {"callbacks": [_langchain_handler_cls()]}
    if session_id:
        config["metadata"] = {"langfuse_session_id": session_id}
    return config


@contextmanager
def trace_span(name: str, **kwargs):
    """Wrap a block of work in a Langfuse span, or a no-op if tracing is disabled.

    Any LangChain calls made inside the block (via langchain_trace_config)
    nest under this span automatically. On disabled/uninitialized tracing, or
    on ANY error opening the span (bad SDK call, auth, network), yields None
    instead of raising - a broken observability call must never take down the
    actual request. This matters especially inside StreamingResponse
    generators: once headers are sent, an exception here can't be recovered
    from the way a normal error handler would.
    """
    if _langchain_handler_cls is None:
        yield None
        return

    from langfuse import get_client

    with ExitStack() as stack:
        try:
            span = stack.enter_context(
                get_client().start_as_current_observation(name=name, **kwargs)
            )
        except Exception:
            logger.exception("Failed to open Langfuse span %r; continuing untraced", name)
            span = None
        # Only span *creation* is guarded above. Anything raised by the
        # caller's `with trace_span(...)` block itself must propagate
        # normally here, not be swallowed as if tracing had failed.
        yield span
