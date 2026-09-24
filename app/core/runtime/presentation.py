"""How a tool call is described to the person watching it run.

Two strings per tool call reach the browser: a label on `tool_start` ("Looking
up the order") and a one-line summary on `tool_result` ("3 courier partners
were available"). Both are written in the agent's domain language, and the
summary is produced by matching on the *shape* of the result dict — which only
the agent that owns the tool can know.

So this is the seam. The runner emits the events; the agent supplies the words.

`DEFAULT_PRESENTATION` handles the shapes every agent produces — an error, a
count, a bare result — so a new agent ships without writing one at all.
"""

from __future__ import annotations

from typing import Protocol


class Presentation(Protocol):
    def tool_label(self, tool_name: str) -> str:
        """Short present-tense phrase for the tool_start event."""

    def summarize(self, tool_name: str, result: dict) -> str:
        """One line describing what came back, for the tool_result event."""


def humanize(tool_name: str) -> str:
    return tool_name.replace("_", " ").capitalize()


class DefaultPresentation:
    """Enough to be useful before an agent writes its own."""

    def tool_label(self, tool_name: str) -> str:
        return humanize(tool_name)

    def summarize(self, tool_name: str, result: dict) -> str:
        if "error" in result:
            return f"Hit a problem: {result['error']}"
        if "count" in result:
            return f"Found {result['count']} results"
        return "Done"


DEFAULT_PRESENTATION: Presentation = DefaultPresentation()
