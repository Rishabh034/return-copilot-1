"""Turn Anthropic token counts into money.

Cost is computed here, locally, from the usage the API already returns - never
fetched from an observability backend. Langfuse computes its own figure from the
same inputs, which makes it a useful independent check but a poor source of truth: 
it flushes in batches,
so the number is not there when a turn ends.

Two conventions carry the whole module:

Money is integer micro-dollars. A micro-dollar is $0.000001. Cents are too
coarse , a single scratchpad call can cost $0.0003, which rounds to zero in
cents and floats accumulate error over thousands of additions. Rates are
therefore stored as integer micros per 1,000 tokens so that even the rate table
avoids fractions: Sonnet's 1.25x cache-write premium is 3750, not 3.75.

Cached tokens are a *subset* of the prompt, not an addition to it. LangChain
reports cache reads and writes inside `input_tokens`, so pricing all three
independently bills the cached portion twice. `_uncached_input_tokens` is where
that subtraction lives, and it is the single most consequential line here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

MICROS_PER_DOLLAR = 1_000_000
_TOKENS_PER_RATE_UNIT = 1_000


class UnknownModelError(LookupError):
    """Raised when a model id has no entry in the rate table.

    Deliberately loud. Pricing an unknown model at zero would silently
    under-count spend against a cap that is supposed to mean real money, and the
    gap would only surface at reconciliation, weeks later.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(
            f"No pricing configured for model {model!r}. "
            f"Add it to MODEL_RATES in {__name__} before using it in production."
        )


@dataclass(frozen=True)
class ModelRates:
    """Integer micro-dollars per 1,000 tokens, per billing class."""

    input: int
    cache_write: int
    cache_read: int
    output: int


# Anthropic list prices as of 2026-08-20. Cache writes are 1.25x base input
# (the 5-minute TTL tier, which is what app/rr_log_analyst/agent/context.py
# requests) and cache reads are 0.1x.
MODEL_RATES: dict[str, ModelRates] = {
    # $3.00 / $15.00 per MTok
    "claude-sonnet-4-6": ModelRates(
        input=3_000, cache_write=3_750, cache_read=300, output=15_000
    ),
    # $1.00 / $5.00 per MTok
    "claude-haiku-4-5": ModelRates(
        input=1_000, cache_write=1_250, cache_read=100, output=5_000
    ),
}


@dataclass(frozen=True)
class TurnCost:
    """Cost of one settled turn, split so a dashboard can explain the total."""

    total_micros: int
    primary_micros: int
    secondary_micros: int

    @property
    def dollars(self) -> float:
        """Display only. Never feed this back into a stored total."""
        return self.total_micros / MICROS_PER_DOLLAR


def rates_for(model: str) -> ModelRates:
    try:
        return MODEL_RATES[model]
    except KeyError:
        raise UnknownModelError(model) from None


def _uncached_input_tokens(usage: Mapping[str, int]) -> int:
    """Prompt tokens billed at full rate.

    `input_tokens` from LangChain is the whole prompt and already contains the
    cached counts, so the cached portion has to come back out before pricing.
    Clamped at zero: a provider that ever reported the parts as disjoint would
    otherwise produce a negative charge.
    """
    return max(
        0,
        (usage.get("input_tokens") or 0)
        - (usage.get("cache_read_input_tokens") or 0)
        - (usage.get("cache_creation_input_tokens") or 0),
    )


def _apply(tokens: int, rate_per_1k: int) -> int:
    return max(0, tokens) * rate_per_1k // _TOKENS_PER_RATE_UNIT


def cost_micros(model: str, usage: Mapping[str, int] | None) -> int:
    """Cost of one model's usage, in micro-dollars.

    `usage` is the dict produced by
    app/rr_log_analyst/agent/context.py::serialize_usage, summed across agent
    iterations by ``merge_usage``. Passing a single iteration's usage would
    under-count the turn.
    """
    if not usage:
        return 0

    rates = rates_for(model)
    return (
        _apply(_uncached_input_tokens(usage), rates.input)
        + _apply(usage.get("cache_creation_input_tokens") or 0, rates.cache_write)
        + _apply(usage.get("cache_read_input_tokens") or 0, rates.cache_read)
        + _apply(usage.get("output_tokens") or 0, rates.output)
    )


def turn_cost(
    *,
    model: str,
    usage: Mapping[str, int] | None,
    secondary_model: str | None = None,
    secondary_usage: Mapping[str, int] | None = None,
) -> TurnCost:
    """Total cost of a turn: the main agent loop plus its scratchpad calls.

    The scratchpad runs a cheaper model and is billed separately; it is real
    spend that no other counter in this service currently records.
    """
    primary = cost_micros(model, usage)
    secondary = (
        cost_micros(secondary_model, secondary_usage)
        if secondary_model and secondary_usage
        else 0
    )
    return TurnCost(
        total_micros=primary + secondary,
        primary_micros=primary,
        secondary_micros=secondary,
    )


def dollars_to_micros(dollars: float) -> int:
    """For config and fixtures only — not for pricing."""
    return round(dollars * MICROS_PER_DOLLAR)


def format_dollars(micros: int) -> str:
    return f"${micros / MICROS_PER_DOLLAR:.2f}"
