"""Response shapes for the internal budget dashboard.

Every money field is an integer micro-dollar count with a `_usd` float beside it.
The integer is authoritative and the float is for display only — it exists so the
client never divides by 1,000,000 itself, and it must never be summed.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

MICROS = 1_000_000


def _usd(micros: int | None) -> float:
    return round((micros or 0) / MICROS, 6)


class OverviewResponse(BaseModel):
    from_: datetime = Field(..., alias="from")
    to: datetime
    turns: int
    cost_micros: int
    support_cost_micros: int = Field(
        ..., description="ClickPost support activity, excluded from customer caps"
    )
    cost_per_turn_micros: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_hit_rate: float = Field(..., description="0-1; share of prompt served from cache")
    active_enterprises: int
    active_users: int
    unpriced_turns: int = Field(
        ..., description="Turns whose model had no rate; recorded but costed at 0"
    )
    period_start: date
    total_capacity_micros: int
    held_now_micros: int
    enterprises_with_budget: int
    enterprises_enforced: int
    enterprises_at_risk: int

    model_config = {"populate_by_name": True}

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)

    @computed_field
    @property
    def cost_per_turn_usd(self) -> float:
        return _usd(self.cost_per_turn_micros)

    @computed_field
    @property
    def total_capacity_usd(self) -> float:
        return _usd(self.total_capacity_micros)


class EnterprisePressure(BaseModel):
    enterprise_id: int
    period_start: date
    period_end: date
    limit_micros: int
    spent_micros: int
    reserved_micros: int
    remaining_micros: int
    consumed_ratio: float = Field(..., description="0-1; spent / limit")
    projected_micros: int = Field(
        ..., description="Run-rate extrapolation of spend to period end"
    )
    projected_over: bool = Field(
        ..., description="True when the run rate breaches the cap before period end"
    )
    days_elapsed: int
    days_total: int
    enforcement_enabled: bool
    seat_count: int | None

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
    def projected_usd(self) -> float:
        return _usd(self.projected_micros)


class PressureResponse(BaseModel):
    period_start: date
    enterprises: list[EnterprisePressure]


class CostClass(BaseModel):
    cls: str = Field(..., description="uncached_input | cache_read | cache_write | output | secondary")
    cost_micros: int
    share: float

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class ModelCost(BaseModel):
    model: str
    turns: int
    cost_micros: int
    unpriced: bool


class BreakdownResponse(BaseModel):
    from_: datetime = Field(..., alias="from")
    to: datetime
    total_micros: int
    by_class: list[CostClass]
    by_model: list[ModelCost]

    model_config = {"populate_by_name": True}


class TrendDay(BaseModel):
    day: date
    turns: int
    cost_micros: int
    cost_per_turn_micros: int
    input_tokens: int
    output_tokens: int
    active_users: int

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class TrendResponse(BaseModel):
    from_: datetime = Field(..., alias="from")
    to: datetime
    days: list[TrendDay]

    model_config = {"populate_by_name": True}


class BlockEvent(BaseModel):
    created_at: datetime
    enterprise_id: int
    user_id: int
    is_internal_user: bool
    limit_kind: str = Field(
        ...,
        description="budget_enterprise | budget_user | quota_user_window — these need different UI",
    )
    limit_micros: int | None
    spent_micros: int | None
    estimate_micros: int | None


class BlocksResponse(BaseModel):
    from_: datetime = Field(..., alias="from")
    to: datetime
    events: list[BlockEvent]

    model_config = {"populate_by_name": True}


class EnterpriseUser(BaseModel):
    user_id: int
    is_internal_user: bool
    turns: int
    cost_micros: int
    input_tokens: int
    output_tokens: int
    last_active: datetime

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class ConversationCost(BaseModel):
    conversation_id: UUID
    enterprise_id: int
    turns: int
    cost_micros: int
    last_active: datetime

    @computed_field
    @property
    def cost_usd(self) -> float:
        return _usd(self.cost_micros)


class EnterpriseDetailResponse(BaseModel):
    from_: datetime = Field(..., alias="from")
    to: datetime
    budget: EnterprisePressure
    users: list[EnterpriseUser]
    days: list[TrendDay]
    top_conversations: list[ConversationCost]

    model_config = {"populate_by_name": True}


class EnterpriseConfigRequest(BaseModel):
    limit_micros: int | None = Field(
        None, gt=0, description="New cap in micro-dollars. $25 = 25000000"
    )
    enforcement_enabled: bool | None = Field(
        None, description="False records spend without ever blocking"
    )
