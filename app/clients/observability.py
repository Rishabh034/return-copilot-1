"""
Wires this app's LLM calls up to self-hosted Langfuse for tracing.

Every LLM call in the API process goes through LangChain's ChatAnthropic
(main agent, scratchpad, and title), so the LangChain CallbackHandler alone
captures all of them. We deliberately do NOT enable the global OTEL
AnthropicInstrumentor here: ChatAnthropic calls the `anthropic` SDK under the
hood, so instrumenting it would trace every call twice (a `ChatAnthropic`
observation from the callback AND an `anthropic.chat` observation from OTEL)
and double-count cost on the parent span. The only raw-SDK caller,
app/agenticintegrations/client.py, runs in a separate consumer process that
never calls init_langfuse(); if that path ever needs tracing, instrument it
there rather than globally here.

No-ops entirely when `settings.langfuse_enabled` is false, so this is safe to
call unconditionally at startup.
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack, contextmanager
from typing import Any

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

    global _langchain_handler_cls
    _langchain_handler_cls = CallbackHandler

    client = get_client()

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


def langfuse_identity(
    *,
    conversation_id: str,
    enterprise_id: int,
    user_id: int,
    is_internal_user: bool,
    internal_user_id: str | None,
    agent_slug: str,
) -> dict[str, Any]:
    """Build kwargs for `trace_span` so cost rolls up agent → enterprise → user → session.

    `agent_slug` is a tag as well as metadata because tags are what the Langfuse
    UI filters on. Without it every agent's traces land in one undifferentiated
    pile, and "what does this agent cost, and how slow is it" is unanswerable —
    with no join to fall back on, because the traces live in another system.
    """
    user_type = "internal" if is_internal_user else "external"
    langfuse_user = langfuse_user_id(
        enterprise_id=enterprise_id,
        user_id=user_id,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
    )
    metadata: dict[str, str] = {
        "agent": agent_slug,
        "enterprise_id": str(enterprise_id),
        "dashboard_user_id": str(user_id),
        "user_type": user_type,
    }
    if internal_user_id:
        metadata["internal_user_id"] = internal_user_id

    return {
        "session_id": conversation_id,
        "user_id": langfuse_user,
        "tags": [
            f"agent:{agent_slug}",
            f"enterprise:{enterprise_id}",
            f"user_type:{user_type}",
        ],
        "metadata": metadata,
    }


def langchain_trace_config(
    session_id: str | None = None,
    *,
    user_id: str | None = None,
    agent_slug: str | None = None,
) -> dict:
    """Build a LangChain `config=` dict that routes a call through Langfuse.

    Returns {} when tracing is disabled, so callers can pass this through
    unconditionally without branching on settings.langfuse_enabled themselves.
    Nested under `trace_span` + propagate_attributes, session/user also inherit
    from the parent; these metadata keys keep Sonnet/Haiku invokes attributable
    even if a call is treated as its own chain root.
    """
    if _langchain_handler_cls is None:
        return {}
    config: dict = {"callbacks": [_langchain_handler_cls()]}
    metadata: dict[str, str] = {}
    if session_id:
        metadata["langfuse_session_id"] = session_id
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if agent_slug:
        metadata["agent"] = agent_slug
    if metadata:
        config["metadata"] = metadata
    return config


@contextmanager
def trace_span(
    name: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
    **kwargs,
):
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

    from langfuse import get_client, propagate_attributes

    with ExitStack() as stack:
        try:
            span = stack.enter_context(
                get_client().start_as_current_observation(name=name, **kwargs)
            )
            stack.enter_context(
                propagate_attributes(
                    user_id=user_id,
                    session_id=session_id,
                    tags=tags,
                    metadata=metadata,
                )
            )
        except Exception:
            logger.exception("Failed to open Langfuse span %r; continuing untraced", name)
            span = None
        # Only span *creation* is guarded above. Anything raised by the
        # caller's `with trace_span(...)` block itself must propagate
        # normally here, not be swallowed as if tracing had failed.
        yield span


def langfuse_user_id(
    *,
    enterprise_id: int,
    user_id: int,
    is_internal_user: bool,
    internal_user_id: str | None,
) -> str:
    if is_internal_user and internal_user_id:
        return f"ent:{enterprise_id}_int:{internal_user_id}"
    return f"ext:{user_id}"
