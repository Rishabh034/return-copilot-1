"""
Wires this app's LLM calls up to self-hosted Langfuse, and its HTTP/LLM/DB
calls up to OpenTelemetry (headed to SigNoz), for tracing.

Every LLM call in the API process goes through LangChain's ChatAnthropic
(main agent, scratchpad, and title), so the LangChain CallbackHandler alone
already captures all of them for Langfuse. init_otel() below additionally
enables the global OTEL AnthropicInstrumentor so the same calls show up as
spans for SigNoz — but ChatAnthropic calls the `anthropic` SDK under the
hood, so both wrap the same call. Langfuse's default span filter exports
spans from *any* known LLM instrumentation library (including this one) in
addition to its own callback-created spans, so left unfiltered this would
trace every call twice in Langfuse too (a `ChatAnthropic` observation from
the callback AND an `anthropic.chat` observation from OTEL) and double-count
cost on the parent span. `_is_langfuse_export_span` below blocks just that
scope from Langfuse's export while leaving it on the shared TracerProvider
for our own OTLP exporter to pick up. The only other raw-SDK caller,
app/agenticintegrations/client.py, runs in a separate consumer process that
never calls init_langfuse(), so it never faces this conflict.

Langfuse's SDK is itself OTEL-based: it claims the global TracerProvider (via
`opentelemetry.trace.set_tracer_provider`, which only succeeds once) on
whichever of init_otel()/init_langfuse() runs first, and attaches its own
export processor to whichever provider is already there otherwise. main.py
calls init_otel() first so our BatchSpanProcessor+OTLPSpanExporter — and our
`service.name` resource attribute — end up on the provider that's actually in
use, regardless of whether Langfuse is enabled. The two stacks then share
that one TracerProvider: our OTLP exporter ships every span (HTTP request,
httpx, SQLAlchemy, LLM) to SigNoz, while Langfuse's own processor ships only
the LLM-shaped ones it's built to display — filtered as described above.

Each half no-ops entirely when its own settings flag (`langfuse_enabled` /
`otel_enabled`) is false, so both are safe to call unconditionally at
startup.
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack, contextmanager, nullcontext
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode

from app.config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.trace import ReadableSpan, TracerProvider

logger = logging.getLogger(__name__)

_langchain_handler_cls: type | None = None
_tracer_provider: "TracerProvider | None" = None
_logger_provider: "LoggerProvider | None" = None

# opentelemetry-instrumentation-anthropic's scope name — see module docstring.
_BLOCKED_LANGFUSE_SCOPES = frozenset({"opentelemetry.instrumentation.anthropic"})


# ClickpostAuth sends an enterprise's license key as `?key=<uuid>` - a
# credential that must never reach a span attribute or a log line.
_REDACTED_QUERY_PARAMS = frozenset({"key"})

# Probes and the docs pages - pure noise as traces.
_FASTAPI_EXCLUDED_URLS = "/health$,/docs,/redoc$,/openapi.json$"


def redact_url(url: str) -> str:
    """`url` with the value of every credential-bearing query param masked."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = [
        (k, "REDACTED" if k.lower() in _REDACTED_QUERY_PARAMS else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _redact_httpx_span(span: Any, request: Any) -> None:
    # The instrumentor has already written the full URL by the time its
    # request hook runs, so overwrite whichever semconv attribute it used.
    if not span.is_recording():
        return
    url = redact_url(str(request.url))
    for attr in ("http.url", "url.full"):
        if attr in (span.attributes or {}):
            span.set_attribute(attr, url)


async def _redact_httpx_span_async(span: Any, request: Any) -> None:
    _redact_httpx_span(span, request)


def _instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument(
        request_hook=_redact_httpx_span,
        async_request_hook=_redact_httpx_span_async,
    )


def _is_langfuse_export_span(span: "ReadableSpan") -> bool:
    from langfuse.span_filter import is_default_export_span

    return is_default_export_span(span) and (
        span.instrumentation_scope is None
        or span.instrumentation_scope.name not in _BLOCKED_LANGFUSE_SCOPES
    )


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

    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    global _langchain_handler_cls
    _langchain_handler_cls = CallbackHandler

    # Explicit kwargs (rather than get_client(), which reads only from env/
    # the default constructor) so we can pass should_export_span - see module
    # docstring. Later get_client() calls (shutdown_langfuse, trace_span)
    # still resolve to this same singleton.
    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
        should_export_span=_is_langfuse_export_span,
    )

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


def init_otel() -> None:
    """Call once at process startup, before instrument_fastapi_app's wrapped
    app handles any requests and before any httpx/DB/Anthropic call is made.

    No-ops entirely when `settings.otel_enabled` is false, so this is safe to
    call unconditionally at startup, same as init_langfuse(). Exports to
    `settings.otel_exporter_otlp_endpoint` over OTLP/gRPC, then instruments
    httpx (ClickpostClient), SQLAlchemy (app/db.py) and the raw Anthropic SDK
    (under LangChain's ChatAnthropic) so their calls become child spans of
    whatever span is active when they're made — the inbound HTTP span from
    instrument_fastapi_app, most often. Only the API process calls this: the
    app/agenticintegrations consumer runs separately and stays untraced.

    Also attaches a logging.Handler to the root logger (app/logging_config.py
    already runs by the time this is called) so every log record this process
    emits - not just its trace_id/span_id, the actual message - ships to
    SigNoz's Logs signal too, tagged with whatever span was active when it was
    logged. That's what makes "what broke, and what did the app log at that
    point" answerable from inside SigNoz directly, rather than needing to grep
    a separate log file by the trace_id app/logging_config.py's filter stamps
    on each line.
    """
    if not settings.otel_enabled:
        return

    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})

    global _tracer_provider
    current_provider = otel_trace.get_tracer_provider()
    if isinstance(current_provider, TracerProvider):
        # Something else (Langfuse's SDK, if init_langfuse() ran first)
        # already claimed the global provider - a second set_tracer_provider
        # call below would silently no-op (OTEL only allows setting it once)
        # and leave our exporter attached to a provider nothing reads from.
        # Share theirs instead of trying to replace it. main.py calls
        # init_otel() before init_langfuse() precisely to avoid landing in
        # this branch, but this stays correct if that order ever changes.
        _tracer_provider = current_provider
    else:
        _tracer_provider = TracerProvider(resource=resource)
        otel_trace.set_tracer_provider(_tracer_provider)

    _tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )

    _instrument_httpx()
    AnthropicInstrumentor().instrument()

    # AsyncEngine has no engine-connect events of its own; it delegates to the
    # sync Engine it wraps, which is what the instrumentor actually listens on.
    from app.db import engine as _db_engine

    SQLAlchemyInstrumentor().instrument(engine=_db_engine.sync_engine)

    # Logs signal - separate provider/exporter from traces, but LoggingHandler
    # reads the currently active span (if any) off the very same context traces
    # use, so a log emitted mid-span arrives in SigNoz pre-tagged with that
    # span's trace_id/span_id - no extra plumbing needed for the correlation.
    global _logger_provider
    _logger_provider = LoggerProvider(resource=resource)
    _logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    logging.getLogger().addHandler(LoggingHandler(logger_provider=_logger_provider))

    logger.info(
        "OTEL tracing + logs enabled (service=%s, endpoint=%s)",
        settings.otel_service_name,
        settings.otel_exporter_otlp_endpoint,
    )


def instrument_fastapi_app(app: "FastAPI") -> None:
    """Wrap `app` so every inbound HTTP request gets its own span.

    Must be called once, right after the FastAPI() constructor — before the
    app handles any requests, but safe to call before init_otel() runs the
    TracerProvider it resolves is looked up lazily at request time. No-op
    unless settings.otel_enabled.
    """
    if not settings.otel_enabled:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls=_FASTAPI_EXCLUDED_URLS)


def shutdown_otel() -> None:
    """Flush buffered spans and log records, and shut down cleanly before the
    process exits."""
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
    if _logger_provider is not None:
        _logger_provider.shutdown()


@contextmanager
def _otel_conversation_span(
    name: str,
    conversation_id: str,
    *,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
):
    """A span for one turn, nested under whatever is active - the inbound HTTP
    request span, so a turn's trace runs from the request down to its DB,
    Clickpost and LLM calls. One trace per turn; `conversation_id` is an
    attribute on both this span and the request span, which is what SigNoz
    filters on to list every turn of a conversation.
    """
    attributes: dict[str, Any] = {"conversation_id": conversation_id}
    if tags:
        attributes["tags"] = tags
    if metadata:
        attributes.update({f"metadata.{k}": v for k, v in metadata.items()})

    otel_trace.get_current_span().set_attribute("conversation_id", conversation_id)
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span


def otel_span(name: str, **attributes: Any):
    """Start a plain OTel span nested under whatever's currently active - most
    often the conversation's `chat_turn` span from `trace_span` below, so a
    call like `with otel_span(f"tool:{tool.name}"):` shows up as a child of
    that conversation's trace, nested as deep as the call site is.

    No-op (returns a null context) when otel is disabled, so callers never
    need to branch on settings.otel_enabled themselves - same convention as
    langchain_trace_config().
    """
    if not settings.otel_enabled:
        return nullcontext()
    return _otel_span(name, **attributes)


@contextmanager
def _otel_span(name: str, **attributes: Any):
    tracer = otel_trace.get_tracer(__name__)
    clean_attrs = {k: v for k, v in attributes.items() if v is not None}
    with tracer.start_as_current_span(name, attributes=clean_attrs) as span:
        yield span


def mark_span_error(span: Any, exc: BaseException, description: str | None = None) -> None:
    """Record `exc` on `span` and set it to ERROR status - use this for a
    failure that was already caught and turned into a normal return value
    (like a tool call's error result), so it still shows up as an errored
    span instead of looking like an ordinary successful one.

    `span` must be one returned by `otel_span()` above (or None, its
    disabled-state value, which this no-ops on) - never the Langfuse span
    `trace_span()` yields, which has a different API (`.update(...)`).
    """
    if span is None:
        return
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, description or str(exc)))


def mark_current_span_error(description: str, exc: BaseException | None = None) -> None:
    """Set whatever span is active to ERROR - for a failure reported to the
    client inside an SSE stream, where the HTTP status is already a 200 on
    the wire and nothing else would flag the turn. No-op when otel is off.
    """
    if not settings.otel_enabled:
        return
    span = otel_trace.get_current_span()
    if exc is not None:
        span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, description))


def record_masked_status(status_code: int) -> None:
    """Put the real status on the request span for a response that goes out as
    a 200 with its status declared to the envelope middleware instead - the
    instrumentor only ever sees the 200. No-op when otel is off.
    """
    if not settings.otel_enabled:
        return
    span = otel_trace.get_current_span()
    span.set_attribute("http.envelope_status_code", status_code)
    if status_code >= 500:
        span.set_status(Status(StatusCode.ERROR, f"envelope status {status_code}"))


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

    Also opens an OTel span for the same block when settings.otel_enabled and
    session_id are both set, nested under the inbound request span and tagged
    with session_id as its conversation_id (see _otel_conversation_span). This
    happens whether or not Langfuse is enabled; when both are, the Langfuse
    span nests under the OTel one and so shares the request's trace id.

    Any LangChain calls made inside the block (via langchain_trace_config)
    nest under this span automatically. On disabled/uninitialized tracing, or
    on ANY error opening the span (bad SDK call, auth, network), yields None
    instead of raising - a broken observability call must never take down the
    actual request. This matters especially inside StreamingResponse
    generators: once headers are sent, an exception here can't be recovered
    from the way a normal error handler would.
    """
    otel_cm = (
        _otel_conversation_span(name, session_id, tags=tags, metadata=metadata)
        if settings.otel_enabled and session_id
        else nullcontext()
    )

    with otel_cm:
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
