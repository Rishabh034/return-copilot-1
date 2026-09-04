"""Response shapes for the customer-facing usage endpoints.

Same money convention as the internal dashboard: every field is an integer
micro-dollar count with a `_usd` float beside it. The integer is authoritative,
the float is for display only so the client never divides by 1,000,000 itself,
and it must never be summed.

Dollars lead and tokens follow throughout. A business user knows what $8.50
means and does not know what 4.4M tokens means — but tokens are what explains an
expensive turn, so both are always present rather than making the client ask
twice.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

MICROS = 1_000_000


def _usd(micros: int | None) -> float:
    return round((micros or 0) / MICROS, 6)


class TokenBreakdown(BaseModel):
    """Tokens behind a spend figure, in the classes that are priced differently."""

    input_tokens: int = Field(
        ..., description="Whole prompt, and already includes the two cache figures"
    )
    output_tokens: int
    cache_read_tokens: int = Field(
        ..., description="Part of input_tokens served from cache at 10% of price"
    )
    cache_creation_tokens: int = Field(
        ..., description="Part of input_tokens written to cache at 125% of price"
    )
    total_tokens: int = Field(..., description="input + output, not a sum of all four")


class TokenWindow(BaseModel):
    """State of the rolling 24-hour rate limit.

    Separate mechanism from the spend cap, and a user can be stopped by either.
    They need different messages: this one clears by itself, the budget does not.
    """

    used_tokens: int = Field(
        ...,
        description=(
            "Input + output together — the limit is charged against the sum, not "
            "against input alone"
        ),
    )
    limit_tokens: int = Field(
        ...,
        description=(
            "max_input + max_output, plus the extra grant once it has been claimed"
        ),
    )
    remaining_tokens: int

    # Broken out because they are what explains an unexpectedly fast burn, but
    # neither is the number the limit is applied to.
    used_input_tokens: int
    used_output_tokens: int
    max_input_tokens: int
    max_output_tokens: int

    extra_tokens: int = Field(
        ..., description="Size of the one-off grant claimable via POST /tokens/demand"
    )
    extra_tokens_claimed: bool = Field(
        ..., description="True once claimed; the grant is already inside limit_tokens"
    )
    window_started_at: datetime
    resets_at: datetime
    exhausted: bool = Field(
        ...,
        description=(
            "True when the next turn would be refused by this limit. Note the window "
            "resets lazily on the next request, so this is false once resets_at passes "
            "even though the counters above still show the old window."
        ),
    )


class PoolState(BaseModel):
    """The enterprise's shared spend pool for the current period."""

    period_start: date
    period_end: date = Field(
        ...,
        description=(
            "Exclusive: the first day of the NEXT period, not the last day of "
            "this one. Subtract a day before showing it as an end date."
        ),
    )
    limit_micros: int
    spent_micros: int
    reserved_micros: int = Field(
        ..., description="Held for turns running right now; released at settlement"
    )
    remaining_micros: int
    consumed_ratio: float = Field(..., description="0-1; spent / limit")
    enforcement_enabled: bool = Field(
        ...,
        description="False means spend is recorded but a turn is never refused for it",
    )

    @computed_field
    @property
    def limit_usd(self) -> float:
        return _usd(self.limit_micros)

    @computed_field
    @property
    def spent_usd(self) -> float:
        return _usd(self.spent_micros)

    @computed_field
    @property
    def reserved_usd(self) -> float:
        return _usd(self.reserved_micros)

    @computed_field
    @property
    def remaining_usd(self) -> float:
        return _usd(self.remaining_micros)


class MyUsageResponse(BaseModel):
    """GET /usage/me — what this person has spent, and what is stopping them."""

    user_id: int
    enterprise_id: int
    is_internal_user: bool
    period_start: date
    period_end: date

    turns: int
    cost_micros: int
    tokens: TokenBreakdown
    last_active: datetime | None

    allocation_micros: int | None = Field(
        None,
        description=(
            "This user's slice of the pool. Null until per-user allocation ships; "
            "a null means the whole remaining pool is available to them."
        ),
    )
    token_window: TokenWindow | None = Field(
        None, description="Null when this user has not sent a message yet"
    )
    pool: PoolState | None = Field(
        None,
        description=(
            "The enterprise pool this spend draws from. Null for internal ClickPost "
            "activity, which is recorded against the enterprise but never charged to it."
        ),
    )

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)

    @computed_field
    @property
    def allocation_usd(self) -> float | None:
        return None if self.allocation_micros is None else _usd(self.allocation_micros)


class EnterpriseUsageResponse(BaseModel):
    """GET /usage/enterprise — the shared pool and what the org has spent."""

    enterprise_id: int
    pool: PoolState

    turns: int
    cost_micros: int = Field(
        ..., description="Spend counted against the pool; excludes ClickPost support"
    )
    tokens: TokenBreakdown
    active_users: int = Field(
        ..., description="Distinct people who used the agent this period"
    )
    seat_count: int | None = Field(
        None, description="Null until clickpost supplies it; needed for per-user splits"
    )
    cost_per_turn_micros: int

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)

    @computed_field
    @property
    def cost_per_turn_usd(self) -> float:
        return _usd(self.cost_per_turn_micros)


class EnterpriseMember(BaseModel):
    """One person's spend, as their colleagues' admin sees it."""

    user_id: int
    turns: int
    cost_micros: int
    input_tokens: int
    output_tokens: int
    last_active: datetime | None
    share_of_spend: float = Field(
        ..., description="0-1; this member's share of what the org spent, not of the cap"
    )
    allocation_micros: int | None = Field(
        None, description="Null until per-user allocation ships"
    )

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class EnterpriseUsersResponse(BaseModel):
    enterprise_id: int
    period_start: date
    period_end: date
    members: list[EnterpriseMember]
    seat_count: int | None = Field(
        None, description="From clickpost's signed claim; null until it ships"
    )
    unallocated_micros: int | None = Field(
        None, description="Pool left after explicit allocations. Null until track E"
    )


class SeriesPoint(BaseModel):
    bucket: datetime = Field(..., description="Start of the day, week or month, UTC")
    turns: int
    cost_micros: int
    input_tokens: int
    output_tokens: int
    active_users: int
    cost_per_turn_micros: int

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class SeriesResponse(BaseModel):
    enterprise_id: int
    granularity: str
    from_: datetime = Field(..., alias="from")
    to: datetime
    points: list[SeriesPoint] = Field(
        ...,
        description=(
            "Only buckets with traffic appear — there is no zero-fill, so a chart "
            "must fill gaps itself or it will draw straight across a quiet week"
        ),
    )

    model_config = {"populate_by_name": True}


class ConversationCost(BaseModel):
    conversation_id: UUID
    user_id: int
    turns: int
    cost_micros: int
    total_tokens: int
    started_at: datetime | None
    last_active: datetime | None

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class ConversationsResponse(BaseModel):
    enterprise_id: int
    period_start: date
    period_end: date
    conversations: list[ConversationCost]


class ConversationTurn(BaseModel):
    """One settled turn. `input_tokens` includes the two cache figures."""

    created_at: datetime
    user_id: int
    model: str
    cost_micros: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    secondary_model: str | None
    secondary_input_tokens: int
    secondary_output_tokens: int
    pricing_failed: bool = Field(
        ..., description="True means no rate was configured, so this turn costed 0"
    )

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class ConversationDetailResponse(BaseModel):
    enterprise_id: int
    conversation_id: UUID
    turns: list[ConversationTurn]
    cost_micros: int

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)
