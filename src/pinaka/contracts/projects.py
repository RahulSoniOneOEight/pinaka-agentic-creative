from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import (
    Field,
)

from ..enums import (
    ApprovalStage,
    ContentMode,
    DeliverableKind,
    GroundingBundleKind,
    ProjectStatus,
)
from .artifacts import DependencyRef
from .common import Money, StrictModel


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=2000)
    mode: ContentMode = ContentMode.PRODUCT
    audience: str = Field(min_length=1, max_length=500)
    output_format: str = Field(default="9:16", min_length=1, max_length=50)
    language: str = Field(default="en", min_length=2, max_length=20)
    duration_seconds: Decimal = Field(default=Decimal(25), gt=0, le=300)
    max_budget: Money
    parent_project_id: str | None = None


class ProjectRecord(StrictModel):
    id: str
    parent_project_id: str | None = None
    slug: str
    name: str
    objective: str
    mode: ContentMode
    audience: str
    output_format: str
    language: str
    duration_seconds: Decimal
    status: ProjectStatus
    max_budget: Money
    reserved: Money
    spent: Money
    version: int
    created_at: str
    updated_at: str


class DeliverableCreate(StrictModel):
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=2000)
    kind: DeliverableKind
    output_format: str = Field(min_length=1, max_length=50)
    language: str = Field(default="en", min_length=2, max_length=20)
    duration_seconds: Decimal | None = Field(default=None, gt=0, le=3600)
    acceptance_targets: dict[str, Any] = Field(default_factory=dict)


class DeliverableRecord(DeliverableCreate):
    id: str
    project_id: str
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class SegmentCreate(StrictModel):
    deliverable_id: str = Field(min_length=1, max_length=120)
    stable_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1, max_length=500)
    duration_seconds: Decimal = Field(gt=0, le=3600)
    narrative: str = Field(min_length=1, max_length=2000)
    constraints: dict[str, Any] = Field(default_factory=dict)


class GroundingBundleCreate(StrictModel):
    kind: GroundingBundleKind
    source_ref: str = Field(min_length=1, max_length=1000)
    values: dict[str, Any] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    effective_from: str | None = None


class FactSetCreate(StrictModel):
    source_ref: str = Field(min_length=1, max_length=1000)
    values: dict[str, Any] = Field(min_length=1)
    effective_from: str | None = None
    evidence_refs: tuple[str, ...] = ()


class ApprovalRequest(StrictModel):
    stage: ApprovalStage
    approved_by: str = Field(min_length=1, max_length=200)
    scope: dict[str, Any] = Field(default_factory=dict)
    output_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    dependencies: tuple[DependencyRef, ...] = ()


class PublicationAuthorizationCreate(StrictModel):
    channels: tuple[str, ...] = Field(min_length=1)
    authorized_by: str = Field(min_length=1, max_length=200)
    reference: str | None = Field(default=None, max_length=500)
    expires_at: str | None = Field(default=None, max_length=50)
