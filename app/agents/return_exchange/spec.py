"""Return Co-pilot — read-only Returns & Exchange assistant."""

from __future__ import annotations

from app.agents.return_exchange.pii import PII_FILTER
from app.agents.return_exchange.prompts import build_context_note, build_system_prompt
from app.agents.return_exchange.tools import create_tools
from app.clients.return_exchange import ReturnExchangeClient
from app.config import settings
from app.core.runtime.policy import ToolGroupPolicy
from app.core.spec import AgentSpec, RuntimeSettings

AGGREGATE_CONFIG = "get_enterprise_return_exchange_configs"

# Longer phrases first so "return window" beats a bare "refund" mention.
CONFIG_BY_PHRASE = (
    ("exchange window", "get_exchange_window_config"),
    ("return window", "get_return_window_config"),
    ("store credit", "get_return_refund_config"),
    ("gift card", "get_return_refund_config"),
    ("quality check", "get_qc_door_step_config"),
    ("warehouse", "get_warehouse_config"),
    ("doorstep", "get_qc_door_step_config"),
    ("refund", "get_return_refund_config"),
    ("reject", "get_rejection_config"),
)


def choose_config(names: set[str], user_text: str | None) -> str:
    """Prefer the aggregate settings tool; otherwise the phrase the user used."""
    if AGGREGATE_CONFIG in names:
        return AGGREGATE_CONFIG
    if user_text:
        text = user_text.lower()
        best: str | None = None
        best_len = -1
        for phrase, tool_name in CONFIG_BY_PHRASE:
            if tool_name in names and phrase in text and len(phrase) > best_len:
                best = tool_name
                best_len = len(phrase)
        if best is not None:
            return best
    return sorted(names)[0]


def build_client() -> ReturnExchangeClient:
    return ReturnExchangeClient(
        base_url=settings.clickpost_base_url,
        timeout=settings.rr_analyst_tool_timeout,
    )


SPEC = AgentSpec(
    slug="return_exchange",
    url_prefix="return-exchange",
    display_name="Return Co-pilot",
    build_tools=create_tools,
    build_system_prompt=build_system_prompt,
    build_context_note=build_context_note,
    runtime=RuntimeSettings.from_settings(),
    output_filter=PII_FILTER,
    tool_groups={"rne_config": ToolGroupPolicy(max_per_turn=1, choose=choose_config)},
    clients={"return_exchange": build_client},
    # Reached through the allocation mount. The chat URL does not change.
    mounted=False,
)
