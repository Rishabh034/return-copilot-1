"""OTel wiring in app/clients/observability.py: what actually lands on a span.

Spans are read back from an in-memory exporter on the process-wide
TracerProvider. init_otel() itself is never called here - it would attach a
gRPC exporter pointed at a collector that is not running - so each test turns
on only the one piece it is about.
"""

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from starlette.testclient import TestClient

from app.clients import observability as obs
from app.clients.clickpost import ClickpostAuth, ClickpostClient
from app.core.auth.agent_token import AgentAuthError
from app.logging_config import _RedactHttpxUrlFilter
from app.main import agent_auth_error_handler

LICENSE_KEY = "3f1c9a52-license-key"
CONVERSATION_ID = "0b6f0c1e-9a53-4f0e-8a54-5d2b7c7f4a11"

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _provider():
    # The global provider can be set once per process, so share whatever is
    # already there rather than assume this module got to it first.
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(_exporter))


@pytest.fixture
def spans(monkeypatch):
    monkeypatch.setattr(obs.settings, "otel_enabled", True)
    _exporter.clear()
    return _exporter.get_finished_spans


@pytest.fixture
def clickpost_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"meta": {"status": 200, "success": True}, "result": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _call_clickpost_with_key(base_url: str) -> None:
    async def call():
        client = ClickpostClient(base_url=base_url, timeout=5)
        try:
            await client._get("/api/x/", ClickpostAuth(key=LICENSE_KEY), {"page": 1})
        finally:
            await client.aclose()

    asyncio.run(call())


def _chat_app() -> FastAPI:
    """A turn shaped like messages.py: the span opens inside the generator a
    StreamingResponse drains after the handler has returned."""
    app = FastAPI()
    app.add_exception_handler(AgentAuthError, agent_auth_error_handler)

    @app.get("/chat/{conversation_id}")
    async def chat(conversation_id: str, fail: bool = False):
        async def stream():
            with obs.trace_span("chat_turn", session_id=conversation_id, tags=["agent:x"]):
                with obs.otel_span("tool:demo", conversation_id=conversation_id):
                    yield "event: text_delta\n\n"
                if fail:
                    obs.mark_current_span_error("llm_error")
                    yield "event: error\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/denied")
    async def denied():
        raise AgentAuthError("Invalid agent token.")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    obs.instrument_fastapi_app(app)
    return app


def _named(spans, name):
    return [s for s in spans if s.name == name]


def _server(spans):
    return [s for s in spans if s.kind is SpanKind.SERVER]


def test_redact_url_masks_only_the_key():
    assert (
        obs.redact_url("https://h/api/x/?page=1&key=secret&q=a+b")
        == "https://h/api/x/?page=1&key=REDACTED&q=a+b"
    )
    assert obs.redact_url("https://h/api/x/") == "https://h/api/x/"
    assert obs.redact_url("https://h/api/x/?page=1") == "https://h/api/x/?page=1"


def test_license_key_never_reaches_the_httpx_span(spans, clickpost_server):
    obs._instrument_httpx()
    try:
        _call_clickpost_with_key(clickpost_server)
    finally:
        HTTPXClientInstrumentor().uninstrument()

    client_spans = [s for s in spans() if s.kind is SpanKind.CLIENT]
    assert client_spans, "the Clickpost call was not traced at all"
    recorded = json.dumps([dict(s.attributes) for s in client_spans], default=str)
    assert LICENSE_KEY not in recorded
    assert "key=REDACTED" in recorded
    # redaction must not cost us the rest of the URL
    assert "page=1" in recorded


def test_license_key_never_reaches_the_httpx_log_line(clickpost_server, caplog):
    httpx_logger = logging.getLogger("httpx")
    redact = _RedactHttpxUrlFilter()
    httpx_logger.addFilter(redact)
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            _call_clickpost_with_key(clickpost_server)
    finally:
        httpx_logger.removeFilter(redact)

    assert "HTTP Request: GET" in caplog.text, "httpx stopped logging requests"
    assert LICENSE_KEY not in caplog.text
    assert "key=REDACTED" in caplog.text


def test_a_turn_is_one_trace_from_request_to_tool(spans):
    client = TestClient(_chat_app())
    assert client.get(f"/chat/{CONVERSATION_ID}").status_code == 200

    finished = spans()
    (server,) = _server(finished)
    (turn,) = _named(finished, "chat_turn")
    (tool,) = _named(finished, "tool:demo")

    trace_id = server.get_span_context().trace_id
    assert turn.get_span_context().trace_id == trace_id
    assert tool.get_span_context().trace_id == trace_id
    assert tool.parent.span_id == turn.get_span_context().span_id
    # the request span is a real root - nothing synthetic above it
    assert server.parent is None

    assert turn.attributes["conversation_id"] == CONVERSATION_ID
    assert server.attributes["conversation_id"] == CONVERSATION_ID
    assert turn.attributes["tags"] == ("agent:x",)


def test_each_turn_of_a_conversation_is_its_own_trace(spans):
    client = TestClient(_chat_app())
    client.get(f"/chat/{CONVERSATION_ID}")
    client.get(f"/chat/{CONVERSATION_ID}")

    turns = _named(spans(), "chat_turn")
    assert len(turns) == 2
    assert turns[0].get_span_context().trace_id != turns[1].get_span_context().trace_id
    assert {t.attributes["conversation_id"] for t in turns} == {CONVERSATION_ID}


def test_error_sent_inside_the_stream_marks_the_turn(spans):
    client = TestClient(_chat_app())
    assert client.get(f"/chat/{CONVERSATION_ID}?fail=true").status_code == 200

    (turn,) = _named(spans(), "chat_turn")
    assert turn.status.status_code is StatusCode.ERROR
    assert turn.status.description == "llm_error"


def test_masked_auth_failure_keeps_its_real_status_on_the_span(spans):
    response = TestClient(_chat_app()).get("/denied")
    real_status = AgentAuthError("x").status_code
    assert response.status_code == 200  # masked on the wire, by design

    (server,) = _server(spans())
    assert server.attributes["http.envelope_status_code"] == real_status


def test_health_probe_is_not_traced(spans):
    assert TestClient(_chat_app()).get("/health").status_code == 200
    assert spans() == ()


def test_everything_is_a_no_op_when_otel_is_off(monkeypatch):
    monkeypatch.setattr(obs.settings, "otel_enabled", False)
    _exporter.clear()

    obs.record_masked_status(401)
    obs.mark_current_span_error("llm_error")
    with obs.trace_span("chat_turn", session_id=CONVERSATION_ID) as span:
        with obs.otel_span("tool:demo") as tool_span:
            assert tool_span is None
    assert span is None
    assert _exporter.get_finished_spans() == ()
