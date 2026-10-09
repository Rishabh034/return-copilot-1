from __future__ import annotations

from app.core.tool_context import ToolContext

CLIENT_NAME = "return_exchange"

# At most one of these runs per model turn. The spec decides which one wins.
CONFIG_GROUP = "rne_config"


def rne_client(context: ToolContext):
    return context.clients[CLIENT_NAME]


def token(context: ToolContext) -> str | None:
    return context.auth.token
