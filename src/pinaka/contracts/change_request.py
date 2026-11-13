from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

ChangeType = Literal[
    "copy", "visual", "motion", "audio", "caption", "metadata", "other"
]
ChangeStatus = Literal["received", "analysed", "approved", "rejected", "applied"]
ChangeClassification = Literal["safe_in_place", "re_render", "re_approval", "blocked"]
ChangeTargetType = Literal[
    "artifact",
    "element",
    "slot",
    "shot",
    "deliverable",
    "audio_bed",
    "audio_cue",
    "experiment",
    "other",
]
ChangePriority = Literal["low", "medium", "high"]
VersionScope = Literal["A", "B", "both", "none"]


class ChangeRequestItemCreate(StrictModel):
    """One addressed change inside a submitted request."""

    target_type: ChangeTargetType
    target_id: str = Field(min_length=1, max_length=200)
    current_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    requested_change: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    priority: ChangePriority = "medium"


class ChangeRequestCreate(StrictModel):
    """Normalized intake form. The generated YAML is one flat request."""

    requested_by: str = Field(min_length=1, max_length=200)
    requested_at: str = Field(min_length=1, max_length=50)
    change_type: ChangeType
    requested_change: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    element_or_artifact_id: str = Field(default="", max_length=200)
    current_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    deadline: str | None = Field(default=None, max_length=50)
    approver: str = Field(min_length=1, max_length=200)
    approval_reference: str | None = Field(default=None, max_length=500)
    version_scope: VersionScope = "none"
    items: tuple[ChangeRequestItemCreate, ...] = ()

    @model_validator(mode="after")
    def request_needs_a_target(self) -> ChangeRequestCreate:
        if not self.element_or_artifact_id.strip() and not self.items:
            raise ValueError(
                "A change request needs element_or_artifact_id or at least one item"
            )
        return self


class ChangeRequestRecord(StrictModel):
    id: str
    project_id: str
    status: ChangeStatus
    document_schema: str
    requested_by: str
    requested_at: str
    change_type: ChangeType
    target_type: ChangeTargetType
    target_id: str
    current_hash: str | None = None
    requested_change: str
    reason: str
    deadline: str | None = None
    approver: str
    approval_reference: str | None = None
    version_scope: VersionScope
    document_hash: str | None = None
    classification: ChangeClassification | None = None
    impact: dict | None = None
    revision_project_id: str | None = None
    created_at: str
    updated_at: str


class ChangeRequestDecision(StrictModel):
    decision: Literal["approve", "reject"]
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
