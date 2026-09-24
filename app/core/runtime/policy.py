"""Per-tool behaviour, declared by the tool instead of hardcoded in the loop.

Three frozensets used to live in the runner, each saying "this tool behaves
differently":

    _DEDUPE_EXEMPT_TOOLS      run_recommendation must re-run; it is a simulation
    _SCRATCHPAD_EXEMPT_TOOLS  the inspect_* tools read the store, not the network
    _CONFIG_FAMILY            (in the Return & Exchange fork) at most one config
                              tool per turn

They are per-tool facts, so they belong on the tool. The platform owns the
mechanism — noticing a group is over budget, skipping the losers, telling the
model why, logging it. The agent owns the judgement of which one wins, because
"prefer the aggregate config tool, else match the user's wording" is domain
knowledge a shared loop cannot hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolPolicy:
    reusable: bool = True
    """May an identical earlier call in this conversation be served from its
    stored result? False for anything whose answer can change between identical
    calls — simulations, "run it again" actions."""

    scratchpad: bool = True
    """Route an oversized result through the summarizer? False for tools that
    read the evidence store rather than fetching, whose results are already
    small and already summarized."""

    group: str | None = None
    """Mutual-exclusion family. At most `ToolGroupPolicy.max_per_turn` of these
    execute in one model turn."""


DEFAULT_POLICY = ToolPolicy()


@dataclass(frozen=True)
class ToolGroupPolicy:
    max_per_turn: int = 1
    choose: Callable[[set[str], str | None], str] | None = None
    """Given the group members requested this turn and the latest user text,
    pick the one that runs. Defaults to a stable alphabetical choice, so the
    behaviour is deterministic even when an agent supplies no judgement."""


def resolve_group_winners(
    requested: dict[str, set[str]],
    groups: dict[str, ToolGroupPolicy],
    user_text: str | None,
) -> dict[str, set[str]]:
    """Return, per group, the tool names that must NOT run this turn.

    Deliberately returns the losers rather than the winner: the caller needs to
    know which calls to answer with POLICY_SKIPPED, and a group that fits inside
    its budget produces no losers at all.
    """
    skipped: dict[str, set[str]] = {}
    for group_name, members in requested.items():
        policy = groups.get(group_name)
        if policy is None or len(members) <= policy.max_per_turn:
            continue
        chooser = policy.choose or (lambda names, _text: sorted(names)[0])
        winner = chooser(set(members), user_text)
        if winner not in members:
            winner = sorted(members)[0]
        losers = set(members) - {winner}
        if losers:
            skipped[group_name] = losers
    return skipped


def with_policy(tool, policy: ToolPolicy):
    """Attach a policy to a LangChain tool and return it.

    `StructuredTool` is a pydantic model and rejects unknown attributes, so this
    goes around the field validation rather than subclassing every tool type.
    Kept in one place so the one unusual line is explained once.
    """
    object.__setattr__(tool, "agent_policy", policy)
    return tool
