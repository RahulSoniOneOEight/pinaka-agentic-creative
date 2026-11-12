from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from pydantic import (
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from ..enums import (
    ProviderExecutionMode,
)
from .common import Money, StrictModel
from .projects import DeliverableCreate, ProjectCreate


class ClientProfile(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    website: HttpUrl
    brand_source_authorized: bool = False
    approved_service_claims: tuple[str, ...] = ()
    cta_label: str = Field(min_length=1, max_length=100)
    cta_url: HttpUrl


class PilotDesignProfile(StrictModel):
    penpot_file_url: HttpUrl
    penpot_file_id: str = Field(min_length=1, max_length=120)
    penpot_page_id: str = Field(min_length=1, max_length=120)
    width_px: int = Field(gt=0, le=16384)
    height_px: int = Field(gt=0, le=16384)
    frame_rate: Decimal = Field(gt=0, le=240)
    font_families: tuple[str, ...] = Field(min_length=1)
    brand_colors: dict[str, str] = Field(min_length=1)

    @field_validator("brand_colors")
    @classmethod
    def validate_brand_colors(cls, value: dict[str, str]) -> dict[str, str]:
        pattern = r"^#[0-9A-Fa-f]{6}$"
        if any(not re.fullmatch(pattern, color) for color in value.values()):
            raise ValueError(
                "brand_colors values must be six-digit hexadecimal colours"
            )
        return value


class PilotScript(StrictModel):
    voiceover: str = Field(min_length=1, max_length=2000)
    on_screen_text: tuple[str, ...] = Field(min_length=1)
    prohibited_claims: tuple[str, ...] = ()


class PilotProviderPolicy(StrictModel):
    mode: ProviderExecutionMode = ProviderExecutionMode.DISABLED
    max_paid_spend: Money
    approved_profile_keys: tuple[str, ...] = ()


class PilotSegmentPlan(StrictModel):
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1, max_length=500)
    duration_seconds: Decimal = Field(gt=0, le=3600)
    narrative: str = Field(min_length=1, max_length=2000)
    constraints: dict[str, Any] = Field(default_factory=dict)


class PilotDeliverablePlan(StrictModel):
    deliverable: DeliverableCreate
    segments: tuple[PilotSegmentPlan, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def segments_must_fit_deliverable(self) -> PilotDeliverablePlan:
        ordinals = [item.ordinal for item in self.segments]
        stable_keys = [item.stable_key for item in self.segments]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Pilot segment ordinals must be unique")
        if len(stable_keys) != len(set(stable_keys)):
            raise ValueError("Pilot segment stable keys must be unique")
        if self.deliverable.duration_seconds is not None:
            total = sum((item.duration_seconds for item in self.segments), Decimal(0))
            if total != self.deliverable.duration_seconds:
                raise ValueError(
                    "Pilot segment durations must equal the deliverable duration"
                )
        return self


class PilotContract(StrictModel):
    project: ProjectCreate
    client: ClientProfile | None = None
    approved_fact_source: str | None = None
    rights_policy: str | None = None
    llm_model: str | None = None
    generation_endpoint: str | None = None
    voice_provider: str | None = None
    design: PilotDesignProfile | None = None
    script: PilotScript | None = None
    provider_policy: PilotProviderPolicy | None = None
    deliverable_plan: PilotDeliverablePlan | None = None
    acceptance_targets: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def product_mode_requires_fact_source_for_lock(self) -> PilotContract:
        policy = self.provider_policy
        if policy:
            if policy.max_paid_spend.currency != self.project.max_budget.currency:
                raise ValueError(
                    "Provider-policy currency must match the project budget currency"
                )
            if policy.max_paid_spend.minor > self.project.max_budget.minor:
                raise ValueError("Provider-policy spend exceeds the project budget")
            if (
                policy.mode == ProviderExecutionMode.DISABLED
                and policy.max_paid_spend.minor != 0
            ):
                raise ValueError("Disabled provider policy must have zero paid spend")
        return self
