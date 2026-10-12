"""Per-request context handed to every tool invocation.

There used to be two of these that disagreed. Allocation's carried
`auth`/`clickpost_client`/`raw_fetch_cache`; the Return & Exchange fork wrote
its own with `token`/`return_exchange_client`/`enterprise_id` — the same
concept, different names, neither reusable.

This is the shared base. An agent that needs more subclasses it and constructs
it in its spec, so its own tools are typed against its own subclass while the
platform only ever touches the fields below.

Clients are a mapping rather than named fields, because an agent's tools reach
different services: allocation talks to Clickpost, Return & Exchange to the R&E
API, the next agent to something else. The spec declares what it needs and the
platform supplies it, instead of every agent adding a field here and a line to
the lifespan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.clients.clickpost import ClickpostAuth

CLICKPOST = "clickpost"
DATABRICKS = "databricks"


@dataclass
class ToolContext:
    auth: ClickpostAuth
    clients: Mapping[str, Any] = field(default_factory=dict)
    conversation_id: str | None = None
    enterprise_id: int | None = None
    enterprise_name: str | None = None
    user_id: int | None = None

    # Memoized raw payloads, keyed by fetch shape. Deliberately scoped to the
    # ToolContext — the runner builds a fresh one per user message — so repeated
    # lookups within one agent loop are free while a user who edits a rule
    # between turns still gets fresh data on the next message.
    fetch_cache: dict[tuple, Any] = field(default_factory=dict)

    @property
    def clickpost_client(self) -> Any:
        """The Clickpost client, by its well-known name.

        Kept as a property because sixteen call sites across the allocation
        tools read it, and renaming them would be a wide diff that no test
        covers: the replay suite drives fake tools, so an attribute typo in a
        real tool would reach production unseen.
        """
        return self.clients.get(CLICKPOST)

    @property
    def databricks_client(self) -> Any:
        """Read-only Databricks SQL client (app/clients/databricks.py)."""
        return self.clients.get(DATABRICKS)

    @property
    def raw_fetch_cache(self) -> dict[tuple, Any]:
        """Former name of `fetch_cache`, for the same reason."""
        return self.fetch_cache
