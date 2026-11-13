from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import (
    Field,
    model_validator,
)

from ..enums import (
    BudgetCategory,
    BudgetPriority,
)
from .common import Money, StrictModel


class BudgetPlanItemCreate(StrictModel):
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    ordinal: int = Field(ge=1)
    category: BudgetCategory
    provider: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=120)
    estimated_cost: Money
    priority: BudgetPriority = BudgetPriority.REQUIRED
    shot_id: str | None = Field(default=None, max_length=120)
    slot_id: str | None = Field(default=None, max_length=120)
    variant_label: Literal["A", "B"] | None = None
    ab_experiment_id: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class BudgetPlanCreate(StrictModel):
    creative_summary: str = Field(min_length=1, max_length=2000)
    items: tuple[BudgetPlanItemCreate, ...] = Field(min_length=1)
    target_amount: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    tolerance_bps: int = Field(default=5000, ge=0, le=10000)
    tolerance_floor_amount: Decimal = Field(
        default=Decimal("0.10"), ge=0, decimal_places=2
    )

    @model_validator(mode="after")
    def plan_items_are_unique(self) -> BudgetPlanCreate:
        keys = [item.stable_key for item in self.items]
        ordinals = [item.ordinal for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Budget item keys must be unique")
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Budget item ordinals must be unique")
        currencies = {item.estimated_cost.currency for item in self.items}
        if len(currencies) != 1:
            raise ValueError("All budget items must share one currency")
        for item in self.items:
            if item.ab_experiment_id and not item.variant_label:
                raise ValueError(
                    "A budget item bound to an A/B experiment needs a variant_label"
                )
        return self

    @property
    def currency(self) -> str:
        return self.items[0].estimated_cost.currency

    @property
    def planned_total_minor(self) -> int:
        return sum(item.estimated_cost.minor for item in self.items)


class BudgetPlanApprovalCreate(StrictModel):
    plan_id: str = Field(min_length=1, max_length=120)
    approved_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class VarianceDecisionCreate(StrictModel):
    request_id: str = Field(min_length=1, max_length=120)
    decision: Literal["approve", "reject"]
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class BudgetRaiseCreate(StrictModel):
    new_max: Money
    raised_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class OverageReconcileCreate(StrictModel):
    job_id: str = Field(min_length=1, max_length=120)
    actual_cost: Money
    reconciled_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
