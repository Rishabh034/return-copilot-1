"""Clickpost-style response envelope.

Every JSON response is wrapped as:

    {
        "meta": {"status": <http status>, "message": <str>, "success": <bool>},
        "data": <original payload>
    }

Implemented as a pure ASGI middleware (same style as RequestIdMiddleware) so it
composes with SSE streaming: anything that isn't application/json — the
text/event-stream from /messages/, static files, HTML — passes through
untouched.
"""

from __future__ import annotations

import json

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# FastAPI's own JSON endpoints that tooling (swagger-ui, redoc) consumes
# directly; wrapping them would break the docs pages.
_EXCLUDED_PATHS = frozenset({"/openapi.json", "/docs", "/redoc"})

ENVELOPE_STATUS_HEADER = "x-envelope-status"


def build_envelope(status_code: int, payload: object) -> dict:
    """Wrap a decoded response payload in the meta/data envelope.

    For error responses the human-readable message is lifted out of the body
    (FastAPI's {"detail": ...} convention) into meta.message.
    """
    success = status_code < 400
    message = "SUCCESS"
    data = payload

    if not success:
        message = "FAILURE"
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        if isinstance(detail, str):
            message = detail
            data = None
        elif isinstance(detail, dict):
            message = str(detail.get("message") or detail.get("error") or message)
            rest = {k: v for k, v in detail.items() if k not in ("message", "error")}
            data = rest or None
        else:
            # e.g. validation errors: detail is a list of error objects
            message = "Validation error" if status_code == 422 else message
            data = detail

    return {
        "meta": {"status": status_code, "message": message, "success": success},
        "data": data,
    }


class ResponseEnvelopeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        body_parts: list[bytes] = []
        wrapping = False

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message, wrapping

            if message["type"] == "http.response.start":
                content_type = Headers(raw=message.get("headers", [])).get(
                    "content-type", ""
                )
                wrapping = content_type.startswith("application/json")
                if wrapping:
                    # Hold the start message until the full body is buffered so
                    # content-length can be rewritten.
                    start_message = message
                else:
                    await send(message)
                return

            if message["type"] == "http.response.body" and wrapping:
                body_parts.append(message.get("body", b""))
                if message.get("more_body"):
                    return

                raw = b"".join(body_parts)
                headers = MutableHeaders(raw=list(start_message.get("headers", [])))

                # A handler that masked its status as 200 declares the real one
                # here. Anything unparseable falls back to the transport status
                # rather than failing the response outright.
                status_code = start_message["status"]
                declared = headers.get(ENVELOPE_STATUS_HEADER)
                if declared is not None:
                    del headers[ENVELOPE_STATUS_HEADER]
                    try:
                        status_code = int(declared)
                    except ValueError:
                        pass

                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = None

                envelope = build_envelope(status_code, payload)
                new_body = json.dumps(envelope, default=str).encode("utf-8")

                headers["content-length"] = str(len(new_body))
                await send({**start_message, "headers": headers.raw})
                await send({"type": "http.response.body", "body": new_body})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)
