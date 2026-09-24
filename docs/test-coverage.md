# Test coverage

What the suite covers, what each test pins down, and what is still unguarded.

## Running it

Pure-logic tests need nothing:

```bash
pytest tests/
```

The integration tests are gated behind an explicit opt-in rather than being
derived from `DATABASE_URL`, so a stray environment variable can never point
them at something real:

```bash
docker compose up -d db
export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
python -m alembic upgrade head
RUN_DB_TESTS=1 pytest tests/
```

Without `RUN_DB_TESTS=1` those files skip rather than fail. Every one of them
uses synthetic enterprise ids far outside the real range and wipes its rows in
a fixture either side, so they are safe against a dev database.

**250 tests: 105 pure, 145 database-backed.**

### One expected local failure

`test_admin_access.py::test_staff_claim_alone_does_not_open_the_dashboard_by_default`
fails on any machine whose `.env` sets `budget_dashboard_open_to_all_staff=True`.
The test asserts the shipped default (`False`); a local override for dev
convenience is what breaks it. It passes in CI.

## The suite at a glance

### Pure logic — no database, always run

| File | Tests | Covers |
| --- | --- | --- |
| `test_agent_auth.py` | 18 | Agent JWT verification, keypair generated per run |
| `test_pricing.py` | 17 | Token usage to micro-dollars, cache pricing, per-model rates |
| `test_internal_actor.py` | 17 | Who gets billed: internal vs external claim resolution |
| `test_license_key_check.py` | 11 | `resolve_license_key` caching and fail-open, HTTP client stubbed |
| `test_budget_period.py` | 10 | Budget period arithmetic and the shape of a refusal |
| `test_cors_alert.py` | 10 | Rejected preflight reaching Slack, with deduping |
| `test_context_trim.py` | 9 | Context-window trimming (detailed below) |
| `test_title_usage.py` | 8 | Conversation-title tokens reaching the ledger |
| `test_registry.py` | 5 | In-memory agent discovery |

### Database-backed — need `RUN_DB_TESTS=1`

| File | Tests | Covers |
| --- | --- | --- |
| `test_user_token_repo.py` | 33 | 24h rate limiter, run against both identities |
| `test_usage_repo.py` | 29 | Customer-facing spend queries and the CSV export |
| `test_admin_access.py` | 22 | Who may open the internal dashboard |
| `test_budget_analytics_repo.py` | 10 | Budget dashboard reads, pressure ranking, projections |
| `test_budget_repo_integration.py` | 10 | Spend caps: hold lifecycle, concurrency guard |
| `test_conversation_repo.py` | 10 | Conversation ownership, expiry, message sequencing |
| `test_integration_key_repo.py` | 9 | Integration-key monthly caps |
| `test_messages_settlement.py` | 9 | The budget hold lifecycle in the router (below) |
| `test_user_chats.py` | 8 | Staff chat-listing scope (below) |
| `test_feedback_repo.py` | 5 | Feedback creation and ownership |

## `test_context_trim.py`

`trim_messages_to_budget` is the last thing to touch a prompt before it ships,
and both failure directions are silent. Trim too little and Anthropic rejects
the request; trim too much, or trim the wrong end, and the model answers
confidently from a mutilated conversation with no error anywhere.

Message costs are measured with `message_token_count` rather than hardcoded, so
a LangChain upgrade cannot fail this file for reasons unrelated to trimming.

| Test | Pins |
| --- | --- |
| `test_a_conversation_within_budget_is_returned_untouched` | Under budget returns the same list object; the common path does not rebuild |
| `test_a_conversation_too_short_to_trim_is_left_alone` | The `len <= tail + 1` guard, where head and tail would otherwise overlap |
| `test_the_opening_message_and_the_recent_tail_survive_a_trim` | Message 0 and the last four always survive, and something was genuinely cut |
| `test_the_survivors_of_the_middle_are_the_most_recent_ones` | Survivors are contiguous with the tail, not scattered holes |
| `test_the_result_is_an_ordered_subset_with_no_duplicates` | Order kept, no repeats, no invented messages |
| `test_trimming_never_returns_more_messages_than_it_was_given` | Output never exceeds input (see below) |
| `test_a_budget_smaller_than_the_protected_messages_still_terminates` | Head plus tail may bust the budget; that is deliberate, not a crash |
| `test_the_system_prompt_is_charged_against_the_same_budget` | A larger system prompt trims more aggressively |
| `test_output_reservation_is_subtracted_from_the_window` | `reserved_output_tokens` leaves the model room to answer |

### The bug this file caught

With `protected_tail_messages = 0`, `annotated[-0:]` selects the **whole list**
rather than an empty one, because `-0 == 0` in Python. The tail slice therefore
protected every message, and the "trimmed" result was the opening message plus
a second copy of the entire conversation — a trimmer that made the prompt
bigger, which is precisely the failure it exists to prevent.

The line computing `trimmable` already guarded this exact trap; the tail slice
did not. Fixed in `app/core/runtime/context.py`:

```python
protected_tail = annotated[-tail_count:] if tail_count else []
```

## `test_messages_settlement.py`

`test_budget_repo_integration.py` proves the repository's `reserve`, `settle`
and `release` each behave. This file proves the *caller* uses them correctly,
which is a separate question: the repository can be flawless while the router
places a hold and never drops it.

The invariant every test defends:

> For every hold `reserve` actually placed, exactly one of `settle` or
> `release` runs, and the reservation ends at zero.

Leak it one way and an abandoned hold sits on the enterprise cap until the
month rolls over, refusing a paying customer capacity nobody is using. Leak it
the other — settling a hold never placed — and the enterprise is credited free
capacity it never bought.

`send_message` is a streaming SSE endpoint wrapped around the whole agent
runtime, so driving it end to end would test LangChain more than settlement.
The two helpers all three of its exit paths funnel through, `_settle_turn` and
`_release_budget_hold`, are where the money actually moves, and are what this
file exercises.

| Test | Pins |
| --- | --- |
| `test_a_settled_turn_drops_the_hold_and_charges_the_measured_cost` | The estimate is admission control only and never reaches the ledger |
| `test_a_turn_that_produced_nothing_still_drops_its_hold` | An empty turn is the easiest hold to leak; it settles at zero |
| `test_a_released_hold_charges_nothing` | The release path leaves spend and reservation at zero |
| `test_a_refused_request_is_never_settled` | A refusal held nothing, so settling would credit free capacity |
| `test_a_refused_request_is_never_released` | The same, for release |
| `test_internal_support_activity_never_touches_the_cap` | Staff take no hold; the ledger row is still written, uncounted |
| `test_settling_only_moves_the_agent_that_placed_the_hold` | Settlement is agent-scoped |
| `test_releasing_only_moves_the_agent_that_placed_the_hold` | Release is agent-scoped |
| `test_a_settle_followed_by_a_release_cannot_drive_the_hold_negative` | A double drop floors at zero and does not double the charge |

The two agent-scoping tests matter because `agent_slug` only became part of
these signatures in `a9f097e`. A mismatched slug matches zero rows and fails
*silently*: the hold is never released and that agent's cap shrinks permanently.

## `test_user_chats.py`

`UserChats._scope` deliberately treats `None` as "do not filter" rather than
"match NULL", because comparing the non-nullable `is_internal_user` column to
None yields `IS NULL` and silently matches nothing. That makes every filter
opt-in, which puts the weight of the module's isolation on its single
unconditional clause: `enterprise_id`.

That asymmetry is what this file pins. Every other filter may be omitted and
the answer merely widens; omit the enterprise scope and one customer's chats
appear in another's listing.

| Test | Pins |
| --- | --- |
| `test_a_listing_never_crosses_enterprises` | Every optional filter defaulted — the shape written by accident — still scopes |
| `test_an_enterprise_with_no_chats_sees_nothing_rather_than_everything` | A missing scope usually shows up as "empty query returns the world" |
| `test_an_unfiltered_listing_includes_both_staff_and_customer_chats` | `None` means unfiltered; documented, so narrowing it later is deliberate |
| `test_filtering_to_customers_excludes_staff_chats` | `is_internal_user=False` |
| `test_filtering_to_staff_excludes_customer_chats` | `is_internal_user=True` |
| `test_the_shared_staff_account_is_split_by_internal_user_id` | Staff share one Django account; the person is `internal_user_id` |
| `test_the_date_window_is_half_open` | `>= frm` and `< to`, so a chat is never in two windows |
| `test_count_and_list_apply_the_same_filters` | `count_chats` drives the page count the API validates against |

`/admin/user-chats` is gated on ClickPost staff rather than on the caller
owning the rows, so nothing here asserts ownership; this module is not the
layer that enforces it.

## `test_usage_repo.py` additions

### `TestExport`

`export_rows` backs `GET /enterprise/export.csv` and is deliberately the one
read in this module with no `is_internal_user` filter. It is a reconciliation
dump rather than a display: the CSV carries `is_internal_user` and
`counted_against_pool` as columns precisely so the reader can tell charged rows
from uncharged ones, and dropping rows would stop the file reconciling against
the ledger. The aggregates elsewhere filter because they are summaries a
customer reads directly.

| Test | Pins |
| --- | --- |
| `test_the_export_is_complete_where_the_summaries_are_filtered` | Both flag combinations present; completeness is the point |
| `test_the_export_never_crosses_enterprises` | Enterprise scope |
| `test_the_export_window_is_half_open` | A row at `period_end` is not billed in two months' exports |
| `test_the_export_is_ordered_oldest_first` | A streamed CSV has to read chronologically |

### A pinned inconsistency

`TestActiveUsers::test_support_is_counted_here_but_excluded_from_the_spend_beside_it`
records today's behaviour rather than blessing it.

`GET /enterprise` returns both numbers in one payload and they disagree about
ClickPost support. `for_enterprise` defaults to `counted_only=True` and leaves
support out because, in its own words, including it "would show a customer
spend they are not paying for and cannot explain". `active_users` has no such
filter, so the same response can tell a four-seat customer that five people
were active while showing spend for four.

Whether that is a bug is a product question nobody has answered. If you are
reading this because the test just failed, you have probably fixed the
inconsistency on purpose — update the test, do not work around it.

## Still unguarded

The repository layer is well covered. The HTTP and runtime layers are close to
zero: no router endpoint has a test beyond the settlement helpers above.

| Area | Risk | Why it matters |
| --- | --- | --- |
| `routers/integrations.py`, `auth/api_key.py` | High | The key repository is tested; the middleware that *enforces* it, and its 402/429 refusals, is not |
| `routers/budget_admin.py` | Medium | `require_clickpost_staff` and `require_clickpost_admin` are tested as dependencies, but nothing catches a new mutation endpoint added without the guard |
| `runtime/runner.py` | Medium | The agent loop: `max_iterations` termination, tool-error handling, malformed tool arguments |
| `billing/usage_ledger_repo.py` | Medium | No dedicated file; exercised only incidentally by two budget tests |
| `runtime/scratchpad.py`, `result_query.py`, `message_mapper.py` | Medium | Chunking thresholds and passthrough boundaries |

Known gaps inside covered modules:

- `test_feedback_repo.py` never exercises the `is_internal_user=True` ownership
  path, though `FeedbackRepository.create` scopes on it.
- `user_token_repo.py`'s `IntegrityError` retry, for two simultaneous first
  requests, never fires — the concurrency test pre-creates the row, so the
  exact race the guard exists for is unproven.
- `pricing.py` prices `secondary_usage` at zero when `secondary_model` is None,
  silently; only the both-absent case is covered.
