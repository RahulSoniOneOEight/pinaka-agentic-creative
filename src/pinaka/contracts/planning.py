from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    Field,
    model_validator,
)

from ..enums import (
    ApprovalStage,
    CreativeElementKind,
    DecisionProvenance,
    DecisionRisk,
)
from .common import StrictModel


class ShotCreate(StrictModel):
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    ordinal: int = Field(ge=1)
    beat: str = Field(min_length=1, max_length=500)
    duration_seconds: Decimal = Field(gt=0, le=300)
    narrative: str = Field(min_length=1, max_length=2000)
    requirements: dict[str, Any] = Field(default_factory=dict)


class SlotCreate(StrictModel):
    shot_id: str
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    role: str = Field(min_length=1, max_length=120)
    identity_requirement: str = Field(min_length=1, max_length=500)
    fact_requirement: str = Field(min_length=1, max_length=500)
    transformation_permission: str = Field(min_length=1, max_length=500)
    text_zone: str | None = Field(default=None, max_length=500)
    approved_sources: tuple[str, ...] = ()
    treatment_ceiling: str = Field(
        default="ai_created",
        pattern=r"^(as_is|match|effects|ai_enhance|composite|ai_created)$",
    )
    exactness: str = Field(
        default="generic",
        pattern=r"^(exact|faithful|generic)$",
    )


class BriefDecisionCreate(StrictModel):
    field: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}$")
    value: Any = None
    state: DecisionProvenance
    basis: str = Field(min_length=1, max_length=2000)
    risk: DecisionRisk = DecisionRisk.LOW
    reversible: bool = True
    alternatives_considered: tuple[str, ...] = ()
    affected_shot_keys: tuple[str, ...] = ()
    review_gate: ApprovalStage | None = None
    blocked_work: tuple[str, ...] = ()

    @model_validator(mode="after")
    def enforce_autonomy_boundary(self) -> BriefDecisionCreate:
        if self.state == DecisionProvenance.ASSUMED and not self.reversible:
            raise ValueError("Assumed decisions must be reversible")
        if self.state == DecisionProvenance.BLOCKED and not self.blocked_work:
            raise ValueError("Blocked decisions must identify the affected work")
        return self


class WorkableBriefCreate(StrictModel):
    source_artifact_id: str = Field(min_length=1, max_length=120)
    source_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    objective: str = Field(min_length=1, max_length=2000)
    audience: str = Field(min_length=1, max_length=500)
    deliverable: str = Field(min_length=1, max_length=500)
    destination: str | None = Field(default=None, max_length=500)
    classification: dict[str, Any] = Field(min_length=1)
    treatment: dict[str, Any] = Field(min_length=1)
    beats: tuple[dict[str, Any], ...] = Field(min_length=1)
    audio_plan: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(min_length=1)
    decisions: tuple[BriefDecisionCreate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def decisions_have_unique_fields(self) -> WorkableBriefCreate:
        fields = [decision.field for decision in self.decisions]
        if len(fields) != len(set(fields)):
            raise ValueError("Brief decision fields must be unique")
        return self


class CreativeElementCreate(StrictModel):
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    kind: CreativeElementKind
    name: str = Field(min_length=1, max_length=200)
    specification: dict[str, Any] = Field(min_length=1)
    continuity: dict[str, Any] = Field(default_factory=dict)
    source_artifact_id: str | None = Field(default=None, max_length=120)


class ShotElementBindingCreate(StrictModel):
    shot_id: str = Field(min_length=1, max_length=120)
    element_id: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class ShotReferenceCreate(StrictModel):
    shot_id: str = Field(min_length=1, max_length=120)
    role: Literal[
        "character",
        "location",
        "style",
        "product",
        "first_frame",
        "last_frame",
        "voice",
    ]
    artifact_id: str | None = Field(default=None, max_length=120)
    element_id: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def exactly_one_source(self) -> ShotReferenceCreate:
        if bool(self.artifact_id) == bool(self.element_id):
            raise ValueError(
                "A shot reference needs exactly one of artifact_id or element_id"
            )
        return self


class TextBriefRequest(StrictModel):
    text: str = Field(min_length=1, max_length=100000)
    audience: str | None = Field(default=None, max_length=500)
    language: str | None = Field(default=None, min_length=2, max_length=20)
    output_format: str | None = Field(default=None, max_length=50)
    destination: str | None = Field(default=None, max_length=100)
    duration_seconds: Decimal | None = Field(default=None, gt=0, le=300)
    create_plan: bool = True
    persist_elements: bool = False
    persist_slots: bool = False
