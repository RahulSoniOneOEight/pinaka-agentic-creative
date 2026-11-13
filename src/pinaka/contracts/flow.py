from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

SourcingDecision = Literal[
    "owned",
    "stock",
    "manual_import",
    "deterministic",
    "generated",
    "not_applicable",
]
FlowStatus = Literal["done", "skipped", "not_applicable", "blocked", "failed"]
Treatment = Literal["as_is", "correct", "ai_enhance", "composite", "generate"]
CandidateRole = Literal["primary", "backup", "variant_a", "variant_b"]
MotionLevel = Literal["M0", "M1", "M2", "M3", "M4", "M5"]


class SourcingDecisionCreate(StrictModel):
    """One per-slot sourcing decision, recorded before any generation."""

    slot_id: str = Field(min_length=1, max_length=120)
    decision: SourcingDecision
    reason: str = Field(min_length=1, max_length=2000)
    providers: tuple[str, ...] = ()
    candidates_considered: int = Field(default=0, ge=0)
    rejection_reason: str | None = Field(default=None, max_length=2000)
    evidence: tuple[str, ...] = ()
    approved_by: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def generated_requires_exhaustion(self) -> SourcingDecisionCreate:
        if self.decision == "generated":
            if not self.providers:
                raise ValueError(
                    "A generated decision must name the stock providers searched"
                )
            if self.candidates_considered <= 0:
                raise ValueError(
                    "A generated decision must record candidates considered"
                )
            if not (self.rejection_reason or "").strip():
                raise ValueError(
                    "A generated decision must record why stock was unsuitable"
                )
        return self


class FlowStepCreate(StrictModel):
    """One append-only G0-G7 step record."""

    step: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$",
        min_length=1,
        max_length=80,
    )
    status: FlowStatus
    reason: str | None = Field(default=None, max_length=2000)
    actor: str = Field(min_length=1, max_length=200)
    tool: str | None = Field(default=None, max_length=200)
    tool_version: str | None = Field(default=None, max_length=200)
    evidence: tuple[str, ...] = ()
    approval_id: str | None = Field(default=None, max_length=120)
    approved_by: str | None = Field(default=None, max_length=200)
    next_action: str | None = Field(default=None, max_length=500)
    trade_off: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def status_requires_evidence_or_reason(self) -> FlowStepCreate:
        if self.status in {"skipped", "not_applicable"}:
            if not (self.reason or "").strip():
                raise ValueError(f"A {self.status} step requires a reason")
            if not (self.approved_by or self.approval_id):
                raise ValueError(
                    f"A {self.status} step requires a named approver "
                    "(approved_by or approval_id)"
                )
        if self.status == "blocked":
            if not (self.reason or "").strip():
                raise ValueError("A blocked step requires a reason")
            if not (self.next_action or "").strip():
                raise ValueError("A blocked step requires a next_action")
        if self.status == "failed" and not (self.reason or "").strip():
            raise ValueError("A failed step requires a reason")
        if self.status == "done" and not self.evidence:
            raise ValueError("A done step requires evidence")
        return self


class SourcingCandidateCreate(StrictModel):
    """One searched candidate in the slot ledger, before the 2-pick."""

    slot_id: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=100)
    provider_asset_id: str = Field(min_length=1, max_length=200)
    media_type: Literal["image", "video", "audio"]
    source_url: str = Field(min_length=1, max_length=2000)
    creator: str | None = Field(default=None, max_length=300)
    licence: str = Field(min_length=1, max_length=300)
    licence_url: str | None = Field(default=None, max_length=2000)
    eligibility: Literal["eligible", "ineligible"]
    eligibility_reason: str | None = Field(default=None, max_length=1000)
    treatment: Treatment
    asset_class: str | None = Field(default=None, max_length=100)
    evidence: tuple[str, ...] = ()
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ineligible_needs_a_reason(self) -> SourcingCandidateCreate:
        if (
            self.eligibility == "ineligible"
            and not (self.eligibility_reason or "").strip()
        ):
            raise ValueError("An ineligible candidate requires a reason")
        return self


class SlotPickCreate(StrictModel):
    """The two-pick: one primary and one independent backup per slot."""

    slot_id: str = Field(min_length=1, max_length=120)
    primary_candidate_id: str = Field(min_length=1, max_length=120)
    backup_candidate_id: str = Field(min_length=1, max_length=120)
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class MotionLevelCreate(StrictModel):
    """The motion level of one shot, recorded before animatic approval."""

    shot_id: str = Field(min_length=1, max_length=120)
    level: MotionLevel
    source: Literal["composition", "generated_video", "stock_clip", "avatar", "manual"]
    evidence: tuple[str, ...] = Field(min_length=1)
    evidence_artifact_id: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class ABExperimentCreate(StrictModel):
    """The A/B contract declared before production."""

    axis: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=1, max_length=1000)
    primary_metric: str = Field(min_length=1, max_length=300)
    traffic_split: str = Field(min_length=1, max_length=100)
    sample_plan: str = Field(min_length=1, max_length=1000)


class ABVariantBind(StrictModel):
    """Bind one rendered variant to an experiment."""

    experiment_id: str = Field(min_length=1, max_length=120)
    label: Literal["A", "B"]
    artifact_id: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)
