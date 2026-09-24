"""Pricing: does a turn's token usage become the right number of micro-dollars?

The bug this guards against is not "the rates are wrong" — a wrong rate is
obvious and easy to fix. It is the cache subtraction. LangChain reports cache
reads and writes *inside* `input_tokens`, so the naive reading of the usage dict
prices the cached portion twice and inflates every cached turn by roughly its
cache-read volume. That over-count is invisible: the numbers still look
plausible, the cap still enforces, and enterprises are simply billed against a
budget faster than they should be. `test_cached_prompt_is_not_billed_twice` is
the one that fails if someone removes that subtraction.

Everything here is integer arithmetic on purpose. Money is never a float.
"""

import pytest

from app.core.billing.pricing import (
    MICROS_PER_DOLLAR,
    MODEL_RATES,
    UnknownModelError,
    cost_micros,
    dollars_to_micros,
    format_dollars,
    rates_for,
    turn_cost,
)

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5"


def usage(inp=0, out=0, cache_read=0, cache_write=0):
    """A serialize_usage()-shaped dict. `inp` is the whole prompt, cache included."""
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }


# --- the anchor -------------------------------------------------------------

def test_worked_example_from_the_spec():
    """A real 3-iteration turn. If this moves, the spec and the code disagree."""
    cost = turn_cost(
        model=SONNET,
        usage=usage(inp=158_000, out=2_200, cache_read=88_000, cache_write=44_000),
        secondary_model=HAIKU,
        secondary_usage=usage(inp=20_000, out=3_000),
    )
    #  uncached 26,000 x 3000/1k =  78,000
    #  write    44,000 x 3750/1k = 165,000
    #  read     88,000 x  300/1k =  26,400
    #  output    2,200 x 15000/1k = 33,000
    #  haiku in 20,000 x 1000/1k =  20,000
    #  haiku out 3,000 x 5000/1k =  15,000
    assert cost.primary_micros == 302_400
    assert cost.secondary_micros == 35_000
    assert cost.total_micros == 337_400
    assert format_dollars(cost.total_micros) == "$0.34"


# --- the subtraction --------------------------------------------------------

def test_cached_prompt_is_not_billed_twice():
    """A fully cached prompt costs the cache-read rate, not read + full input."""
    cached = cost_micros(SONNET, usage(inp=100_000, cache_read=100_000))
    assert cached == 100_000 * 300 // 1000 == 30_000

    uncached = cost_micros(SONNET, usage(inp=100_000))
    assert uncached == 300_000

    # The whole point of the cache: a 10x saving, not a 1.1x surcharge.
    assert cached * 10 == uncached


def test_cache_write_carries_its_premium():
    """First use of a prefix costs 1.25x, which is *more* than uncached input."""
    written = cost_micros(SONNET, usage(inp=44_000, cache_write=44_000))
    plain = cost_micros(SONNET, usage(inp=44_000))
    assert written == 44_000 * 3750 // 1000 == 165_000
    assert written > plain


def test_mixed_prompt_splits_across_the_three_input_classes():
    """26k uncached + 88k read + 44k written, all inside one input_tokens figure."""
    got = cost_micros(SONNET, usage(inp=158_000, cache_read=88_000, cache_write=44_000))
    assert got == 78_000 + 26_400 + 165_000


def test_cache_larger_than_input_clamps_instead_of_going_negative():
    """Defensive: a provider reporting the parts as disjoint must not credit us."""
    got = cost_micros(SONNET, usage(inp=1_000, cache_read=5_000))
    assert got == 5_000 * 300 // 1000
    assert got >= 0


# --- rates ------------------------------------------------------------------

def test_output_is_the_expensive_direction():
    """Output is 5x input on Sonnet — the reason shorter answers beat cheaper prompts."""
    assert cost_micros(SONNET, usage(out=1_000)) == 5 * cost_micros(SONNET, usage(inp=1_000))


def test_haiku_is_a_third_of_sonnet_on_input():
    assert cost_micros(HAIKU, usage(inp=90_000)) * 3 == cost_micros(SONNET, usage(inp=90_000))


@pytest.mark.parametrize("model", list(MODEL_RATES))
def test_every_rate_is_a_positive_integer(model):
    """Fractional rates would reintroduce float money through the back door."""
    rates = rates_for(model)
    for field in ("input", "cache_write", "cache_read", "output"):
        value = getattr(rates, field)
        assert isinstance(value, int) and value > 0, f"{model}.{field} = {value!r}"


@pytest.mark.parametrize("model", list(MODEL_RATES))
def test_cache_read_is_cheapest_and_output_dearest(model):
    r = rates_for(model)
    assert r.cache_read < r.input < r.cache_write < r.output


# --- contract ---------------------------------------------------------------

def test_unknown_model_raises_rather_than_pricing_at_zero():
    with pytest.raises(UnknownModelError) as exc:
        cost_micros("claude-opus-9-nonexistent", usage(inp=1_000))
    assert "claude-opus-9-nonexistent" in str(exc.value)


@pytest.mark.parametrize("empty", [None, {}])
def test_absent_usage_is_free_and_does_not_need_a_known_model(empty):
    """A turn that produced no usage must not raise on an unpriced model."""
    assert cost_micros("anything-at-all", empty) == 0


def test_scratchpad_is_optional():
    only_primary = turn_cost(model=SONNET, usage=usage(inp=1_000))
    assert only_primary.secondary_micros == 0
    assert only_primary.total_micros == only_primary.primary_micros


def test_result_is_always_an_int():
    """Anything that reaches a bigint column must not be a float."""
    cost = turn_cost(
        model=SONNET,
        usage=usage(inp=7_777, out=333, cache_read=1_111, cache_write=222),
        secondary_model=HAIKU,
        secondary_usage=usage(inp=999, out=11),
    )
    for value in (cost.total_micros, cost.primary_micros, cost.secondary_micros):
        assert isinstance(value, int)


def test_dollars_round_trip_for_config_values():
    assert dollars_to_micros(25) == 25 * MICROS_PER_DOLLAR
    assert dollars_to_micros(0.50) == 500_000
    assert format_dollars(25_000_000) == "$25.00"
