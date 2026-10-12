"""ClickHelp — the carrier allocation analyst.

The whole agent, declared. Everything it is made of lives beside this file.
"""

from __future__ import annotations

from app.agents.allocation.presentation import PRESENTATION
from app.agents.allocation.prompts import build_context_note, build_system_prompt
from app.agents.allocation.tools import create_tools
from app.core.spec import AgentSpec, RuntimeSettings

SPEC = AgentSpec(
    slug="allocation",
    # Its existing, published URL. Clients depend on it, so it stays whatever
    # the package is called internally.
    url_prefix="rr-log-analyst",
    display_name="ClickHelp",
    build_tools=create_tools,
    build_system_prompt=build_system_prompt,
    build_context_note=build_context_note,
    runtime=RuntimeSettings.from_settings(),
    presentation=PRESENTATION,
    # The dashboard keeps calling /api/rr-log-analyst/. This mount is the
    # entry for both agents; the orchestrator picks one per new conversation.
    routes=("allocation", "return_exchange"),
)
