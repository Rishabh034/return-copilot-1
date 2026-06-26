from __future__ import annotations

from contextvars import ContextVar

# One unique ID per HTTP request, automatically available in every log line.
# Equivalent to Java's MDC (Mapped Diagnostic Context) — each async task
# gets its own isolated value, so concurrent requests never see each other's IDs.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
