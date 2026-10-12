"""Pick which agent answers a turn on a shared chat URL.

The dashboard keeps calling one mount. A new conversation is routed from the
first message. A conversation that already has messages stays on the agent
stamped on it, so a follow-up like "why?" cannot jump agents.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.clients.llm import create_secondary_chat_model
from app.core import registry
from app.core.runtime.context import serialize_usage
from app.core.spec import AgentSpec

logger = logging.getLogger(__name__)

_AGENTS = frozenset({"allocation", "return_exchange"})

# Longer, more specific phrases weigh more. A bare "return" is weak on purpose:
# "return the log" is not a returns question.
_RETURN_EXCHANGE = (
    ("return and exchange", 4),
    ("returns and exchange", 4),
    ("return & exchange", 4),
    ("reverse awb", 4),
    ("reverse pickup", 4),
    ("payment reversal", 4),
    ("store credit", 4),
    ("return window", 4),
    ("exchange window", 4),
    ("non-returnable", 4),
    ("non returnable", 4),
    ("return request", 4),
    ("exchange request", 4),
    ("return label", 3),
    ("refund", 3),
    ("gift card", 3),
    ("reverse logistics", 3),
    ("r&e", 3),
    ("doorstep", 2),
    ("exchange", 2),
    ("return", 2),
)

_ALLOCATION = (
    ("recommendation", 4),
    ("serviceability", 4),
    ("allocation rule", 4),
    ("serviceable", 3),
    ("allocation", 3),
    ("courier", 3),
    ("carrier", 3),
    ("pincode", 3),
)

_ROUTER_SYSTEM = (
    "Route a Clickpost ops question to exactly one agent. "
    "Reply with one word and nothing else: allocation or return_exchange.\n"
    "allocation: courier recommendation, serviceability, why a carrier was picked, "
    "allocation rules, pincode, shipping partner.\n"
    "return_exchange: a return or exchange request, refund, reverse pickup, "
    "store credit, R&E settings, return or exchange window."
)

Completer = Callable[[str], Awaitable[tuple[str, dict[str, int] | None]]]


def _score(text: str, phrases: tuple[tuple[str, int], ...]) -> int:
    return sum(weight for phrase, weight in phrases if phrase in text)


def route_by_rules(text: str) -> str | None:
    """Return a slug when the wording is clearly one agent, else None."""
    lowered = text.lower()
    returns = _score(lowered, _RETURN_EXCHANGE)
    allocation = _score(lowered, _ALLOCATION)
    if returns >= 3 and returns >= allocation + 2:
        return "return_exchange"
    if allocation >= 3 and allocation >= returns + 2:
        return "allocation"
    return None


def parse_agent_name(text: str) -> str | None:
    cleaned = text.strip().lower()
    if not cleaned:
        return None
    token = cleaned.split()[0].strip(".,:;\"'`")
    if token in _AGENTS:
        return token
    return None


def _coerce_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return ""


async def _default_complete(text: str) -> tuple[str, dict[str, int] | None]:
    llm = create_secondary_chat_model()
    response = await llm.ainvoke(
        [
            SystemMessage(content=_ROUTER_SYSTEM),
            HumanMessage(content=text[:2000]),
        ]
    )
    usage = serialize_usage(getattr(response, "usage_metadata", None))
    return _coerce_text(response.content), usage


async def classify_message(
    text: str,
    *,
    complete: Completer | None = None,
    fallback: str = "allocation",
) -> tuple[str, dict[str, int] | None]:
    """Pick an agent slug. Rules first; the small model only when they abstain.

    A model failure, or an answer that is not an agent name, uses `fallback`.
    The usage half is None when no model call was made.
    """
    ruled = route_by_rules(text)
    if ruled is not None:
        return ruled, None

    complete = complete or _default_complete
    try:
        answer, usage = await complete(text)
    except Exception:
        logger.exception("Agent router failed; using %s", fallback)
        return fallback, None
    parsed = parse_agent_name(answer)
    if parsed is None:
        logger.info("Agent router returned %r; using %s", answer, fallback)
        return fallback, usage
    return parsed, usage


async def choose_agent(
    entry: AgentSpec,
    message: str,
    *,
    locked_slug: str | None = None,
    complete: Completer | None = None,
) -> tuple[AgentSpec, dict[str, int] | None]:
    """The spec that should run this turn, plus any router-model usage.

    An entry with no `routes` is returned as-is. A locked slug is a
    conversation that already has messages.
    """
    if not entry.routes:
        return entry, None
    if locked_slug:
        if locked_slug in entry.routes:
            return registry.get(locked_slug), None
        return entry, None
    slug, usage = await classify_message(message, complete=complete, fallback=entry.slug)
    if slug not in entry.routes:
        slug = entry.slug
    return registry.get(slug), usage
