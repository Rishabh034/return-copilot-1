"""A complete agent, as small as one can be. Copy this directory to start.

`enabled=False` is deliberate and load-bearing. The registry still imports this
package, so the example cannot silently rot — a change to AgentSpec that breaks
it breaks the build — but `registry.enabled()` filters it out, so it mounts no
routes and serves no traffic. Deleting the flag publishes /api/example/ to the
internet; do not.
"""

from __future__ import annotations

from app.agents.example.prompts import build_context_note, build_system_prompt
from app.agents.example.tools import create_tools
from app.core.spec import AgentSpec, RuntimeSettings

SPEC = AgentSpec(
    slug="example",
    url_prefix="example",
    display_name="Example Agent",
    build_tools=create_tools,
    build_system_prompt=build_system_prompt,
    build_context_note=build_context_note,
    # from_settings() inherits today's model and limits. Override any field to
    # diverge, e.g. RuntimeSettings.from_settings(model="claude-haiku-4-5",
    # secondary_model="claude-haiku-4-5") for an agent that should run cheap.
    runtime=RuntimeSettings.from_settings(),
    # No presentation: the default describes errors and counts well enough to
    # ship without one. Add app/agents/example/presentation.py when the agent's
    # own result shapes deserve better words.
    # No tool_groups and no output_filter: both are opt-in.
    enabled=False,
)
