"""One vocabulary for tool failures.

The problem this replaces: `_invoke_tool` produced five different shapes, two
of which carried a `code` and three of which carried only an English sentence.
`_summarize_result` then collapsed every one of them into

    "Hit a problem: <raw internal text>"

so the browser could not tell "you lack access" from "the upstream 500'd" from
"our own bug", and the raw text — endpoint paths, HTTP status codes, upstream
messages like "Oops! Internal server error" — was handed to the model with only
a paragraph of prompt asking it not to repeat them.

The root cause is that one string was serving three audiences. They are
separated here:

    detail        engineers. Logs and Slack only. Never leaves the server.
    model_message the model. Sanitized, and says what to do about it.
    summary       the user. Derived from the code, never from the raw text.

`retryable` turns "retry once if plausible" from a prompt instruction into
something the loop can act on.

The codes are the point: a code is greppable, alertable and chartable. A rising
INTERNAL or UPSTREAM_BAD_RESPONSE is an incident signal we currently cannot see
at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolErrorCode(StrEnum):
    TIMEOUT = "timeout"
    """Our own wait budget expired."""

    UPSTREAM_TIMEOUT = "upstream_timeout"
    """A downstream service was too slow."""

    UPSTREAM_ERROR = "upstream_error"
    """A downstream service answered with a failure."""

    UPSTREAM_BAD_RESPONSE = "upstream_bad_response"
    """A downstream service answered, and we could not parse it."""

    PERMISSION_DENIED = "permission_denied"
    """The current credential cannot reach those records."""

    NOT_FOUND = "not_found"
    """Resolved correctly, and genuinely absent."""

    RATE_LIMITED = "rate_limited"

    INVALID_ARGUMENTS = "invalid_arguments"
    """The model's own call was malformed. It can fix this and retry."""

    POLICY_SKIPPED = "policy_skipped"
    """Suppressed by tool policy, not attempted. See runtime/policy.py."""

    INTERNAL = "internal"
    """Our bug."""


# What the user is told, per code. Deliberately says nothing about servers,
# status codes, outages or "our end": a failed lookup is only ever "these
# records could not be loaded right now".
_USER_SUMMARY: dict[ToolErrorCode, str] = {
    ToolErrorCode.TIMEOUT: "That lookup took too long",
    ToolErrorCode.UPSTREAM_TIMEOUT: "Those records could not be loaded right now",
    ToolErrorCode.UPSTREAM_ERROR: "Those records could not be loaded right now",
    ToolErrorCode.UPSTREAM_BAD_RESPONSE: "Those records could not be read right now",
    ToolErrorCode.PERMISSION_DENIED: "You do not have access to those records",
    ToolErrorCode.NOT_FOUND: "Nothing matched",
    ToolErrorCode.RATE_LIMITED: "Too many lookups at once — slowing down",
    ToolErrorCode.INVALID_ARGUMENTS: "Retrying that lookup",
    ToolErrorCode.POLICY_SKIPPED: "Skipped — already covered",
    ToolErrorCode.INTERNAL: "Those records could not be loaded right now",
}

_RETRYABLE = frozenset(
    {
        ToolErrorCode.TIMEOUT,
        ToolErrorCode.UPSTREAM_TIMEOUT,
        ToolErrorCode.UPSTREAM_ERROR,
        ToolErrorCode.RATE_LIMITED,
        ToolErrorCode.INVALID_ARGUMENTS,
    }
)


@dataclass(frozen=True)
class ToolError:
    code: ToolErrorCode
    tool_name: str
    model_message: str
    detail: str
    """Engineer-facing. Carries endpoints, status codes and upstream text, and
    must never be placed in a tool result or an SSE payload."""

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    @property
    def user_summary(self) -> str:
        return _USER_SUMMARY[self.code]

    def as_tool_result(self) -> dict[str, Any]:
        """What the model receives. `detail` is deliberately absent."""
        return {
            "error": self.model_message,
            "code": str(self.code),
            "retryable": self.retryable,
        }


def tool_error(
    code: ToolErrorCode,
    *,
    tool_name: str,
    detail: str,
    model_message: str | None = None,
) -> ToolError:
    return ToolError(
        code=code,
        tool_name=tool_name,
        model_message=model_message or _MODEL_MESSAGE[code],
        detail=detail,
    )


# What the model is told, per code. Says what it means and what to do, without
# any of the upstream text that produced it.
_MODEL_MESSAGE: dict[ToolErrorCode, str] = {
    ToolErrorCode.TIMEOUT: "This lookup timed out. Retry it once; if it fails again, tell the user those records could not be loaded right now.",
    ToolErrorCode.UPSTREAM_TIMEOUT: "This lookup timed out. Retry it once; if it fails again, tell the user those records could not be loaded right now.",
    ToolErrorCode.UPSTREAM_ERROR: "This lookup failed transiently. Retry it once; if it fails again, tell the user those records could not be loaded right now.",
    ToolErrorCode.UPSTREAM_BAD_RESPONSE: "This lookup returned something unreadable. Do not retry. Tell the user those records could not be read right now.",
    ToolErrorCode.PERMISSION_DENIED: "The current login cannot reach these records. Do not retry. Say the records could not be accessed and suggest checking with their Clickpost administrator. Do not speculate about which permission is missing.",
    ToolErrorCode.NOT_FOUND: "Nothing matched. This is a finding, not a failure — say plainly that no records matched, and what you searched for.",
    ToolErrorCode.RATE_LIMITED: "Too many lookups at once. Wait before retrying, and prefer fewer, broader calls.",
    ToolErrorCode.INVALID_ARGUMENTS: "Your arguments were malformed. Correct them and call again. Do not mention this to the user.",
    ToolErrorCode.POLICY_SKIPPED: "Skipped: this turn already covers it. Use the result you were given.",
    ToolErrorCode.INTERNAL: "This lookup could not be completed. Do not retry. Tell the user those records could not be loaded right now.",
}


_BY_VALUE = {str(code): code for code in ToolErrorCode}


def summary_for_result(result: dict[str, Any]) -> str | None:
    """The user-facing line for a tool result that is an error, or None.

    Error wording is derived from the code and is the same for every agent —
    "you do not have access" does not vary by domain — so the runner answers it
    here rather than asking the agent's presentation, which only knows the
    shapes of its own successful results.
    """
    code = _BY_VALUE.get(str(result.get("code", "")))
    if code is None:
        return None
    return _USER_SUMMARY[code]
