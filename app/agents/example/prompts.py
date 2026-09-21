"""This agent's prompt. Nothing here is shared with any other agent.

Two halves, split at the prompt-cache breakpoint:

  build_system_prompt   identical for every user and every message, so it is
                        cached. Takes no arguments by design — the cache matches
                        on exact bytes, and anything request-specific here would
                        give every caller their own cache entry instead of one
                        entry shared across the organization.
  build_context_note    who is asking, and when. Sent after the breakpoint and
                        never cached.
"""

from __future__ import annotations

from pathlib import Path

from app import clock
from app.core.prompting import load_knowledge

# This agent's own knowledge base. Scoped to this package, so no other agent's
# documents can reach this prompt.
_KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

_ROLE = """\
You are the Example Agent. You exist to show the shape of an agent, not to
answer real questions. Say so if anyone asks you something substantive.
"""


def build_system_prompt() -> str:
    return "\n".join([_ROLE, "", load_knowledge(_KNOWLEDGE_DIR)])


def build_context_note(
    *,
    enterprise_id: int | None = None,
    message_time: str | None = None,
    enterprise_name: str | None = None,
    username: str | None = None,
) -> str:
    now = clock.now_ist()
    lines = [f"- Current date and time (IST): **{now.strftime('%Y-%m-%d %H:%M')}**."]
    if enterprise_id is not None:
        lines.append(f"- Assisting enterprise {enterprise_id}.")
    if username:
        lines.append(f"- User: **{username}**.")
    return "\n## Context\n\n" + "\n".join(lines)
