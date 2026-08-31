"""Budget period arithmetic and the shape of a refusal.

The DB-touching parts of BudgetRepository (reserve, settle, release) need a live
Postgres and are exercised by the curl walkthrough in docs/budget-curls.md
rather than here. What *is* worth pinning in a unit test is the calendar
arithmetic, because it fails once a year in December and silently: a naive
`month + 1` produces month 13, and either raises on the last day of the year or,
worse, quietly puts January's spend in a row nobody reads.
"""

from datetime import date, datetime, timezone

import pytest

from app.rr_log_analyst.repositories.budget_repo import BudgetState, period_bounds


def at(year, month, day=15, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "moment,expected_start,expected_end",
    [
        (at(2026, 1), date(2026, 1, 1), date(2026, 2, 1)),
        (at(2026, 6), date(2026, 6, 1), date(2026, 7, 1)),
        (at(2026, 11), date(2026, 11, 1), date(2026, 12, 1)),
        # The one that breaks a naive implementation.
        (at(2026, 12), date(2026, 12, 1), date(2027, 1, 1)),
    ],
)
def test_period_is_the_calendar_month(moment, expected_start, expected_end):
    assert period_bounds(moment) == (expected_start, expected_end)


@pytest.mark.parametrize("day,hour", [(1, 0), (15, 12), (28, 23)])
def test_every_moment_in_a_month_maps_to_the_same_row(day, hour):
    """Otherwise a turn at 23:59 would create a second row for the same month."""
    assert period_bounds(datetime(2026, 2, day, hour, tzinfo=timezone.utc)) == (
        date(2026, 2, 1),
        date(2026, 3, 1),
    )


def test_year_boundary_is_contiguous():
    """December's end must be January's start, or a day of spend has no home."""
    _, dec_end = period_bounds(at(2026, 12))
    jan_start, _ = period_bounds(at(2027, 1))
    assert dec_end == jan_start


def state(limit, spent, reserved, estimate=500_000, allowed=True):
    return BudgetState(
        allowed=allowed,
        enforcement_enabled=True,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 11, 1),
        limit_micros=limit,
        spent_micros=spent,
        reserved_micros=reserved,
        estimate_micros=estimate,
    )


def test_remaining_subtracts_holds_as_well_as_spend():
    """Money held for in-flight turns is not available, even though it is unspent."""
    assert state(25_000_000, 20_000_000, 1_000_000).remaining_micros == 4_000_000


def test_remaining_never_goes_negative():
    """A turn that overran its hold must show an empty pool, not a negative one."""
    assert state(25_000_000, 26_000_000, 0).remaining_micros == 0
