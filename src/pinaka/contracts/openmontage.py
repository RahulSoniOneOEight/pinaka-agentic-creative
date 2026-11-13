from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel
from .production_plan import ProductionMethod


class CapabilityEntry(StrictModel):
    capability_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=120)
    planning_use: str = Field(min_length=1, max_length=1000)
    production_method: ProductionMethod
    cost_hint: str = Field(min_length=1, max_length=200)
    account_requirement: str = Field(min_length=1, max_length=300)
    fallback: str = Field(min_length=1, max_length=1000)
    available: bool = True


class CapabilityDiscoveryCreate(StrictModel):
    source: str = Field(min_length=1, max_length=500)
    account_ready: bool = False
    credits: int = Field(default=0, ge=0)
    providers: tuple[str, ...] = ()
    capabilities: tuple[CapabilityEntry, ...] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def capabilities_are_unique(self) -> CapabilityDiscoveryCreate:
        ids = [item.capability_id for item in self.capabilities]
        if len(ids) != len(set(ids)):
            raise ValueError("Capability ids must be unique")
        return self


class CapabilityRoute(StrictModel):
    capability_id: str
    ready: bool
    action: Literal["studio", "fallback"]
    fallback: str
    reason: str
