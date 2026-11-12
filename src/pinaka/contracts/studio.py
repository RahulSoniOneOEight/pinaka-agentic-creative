from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import (
    Field,
    model_validator,
)

from ..enums import (
    ApprovalStage,
    ArtifactKind,
    CapabilityStatus,
    StudioAutonomy,
    StudioWorkPhase,
    ToolOperationStatus,
)
from .common import Money, StrictModel


class StudioScope(StrictModel):
    autonomy: StudioAutonomy = StudioAutonomy.AUTOPILOT
    phases: tuple[StudioWorkPhase, ...] = Field(min_length=1)
    shot_ids: tuple[str, ...] = ()
    element_ids: tuple[str, ...] = ()
    constraints: dict[str, Any] = Field(default_factory=dict)
    allow_paid_generation: bool = True
    max_credits: int = Field(default=0, ge=0)
    max_amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    max_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    allow_publication: bool = False

    @model_validator(mode="after")
    def enforce_spend_shape_and_no_publication(self) -> StudioScope:
        if self.max_credits:
            raise ValueError("Studio work packets use a money reservation, not credits")
        if (self.max_amount is None) != (self.max_currency is None):
            raise ValueError("max_amount and max_currency must be supplied together")
        if self.max_amount is not None:
            if bool(self.max_amount) != self.allow_paid_generation:
                raise ValueError(
                    "Paid generation must be enabled exactly when max_amount is positive"
                )
        elif not self.allow_paid_generation:
            raise ValueError(
                "A zero-cost run must declare max_amount 0 in the project currency"
            )
        if self.allow_publication:
            raise ValueError("Studio publication authority is not permitted")
        if len(self.phases) != len(set(self.phases)):
            raise ValueError("Studio phases must be unique")
        return self


class StudioWorkPacketRequest(StrictModel):
    tool_profile_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}$")
    instructions: str = Field(min_length=1, max_length=10000)
    scope: StudioScope
    input_artifact_ids: tuple[str, ...] = ()
    output_declaration: dict[str, Any] = Field(min_length=1)
    required_approval_stage: ApprovalStage | None = None
    spend_approval_id: str | None = Field(default=None, max_length=120)
    request_generated_motion: bool = False
    generated_motion_reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def generated_motion_is_explicit(self) -> StudioWorkPacketRequest:
        if self.request_generated_motion and not self.generated_motion_reason:
            raise ValueError(
                "Generated motion is an exception and requires a stated reason"
            )
        return self


class StudioSpendApprovalCreate(StrictModel):
    work_packet: StudioWorkPacketRequest
    approved_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def approval_is_only_for_exception_spend(self) -> StudioSpendApprovalCreate:
        if self.work_packet.scope.max_amount is None:
            raise ValueError(
                "Spend approval requires an explicit max_amount and max_currency"
            )
        if self.work_packet.spend_approval_id:
            raise ValueError("Approval requests cannot reference an existing approval")
        return self


class StudioResultOutputImport(StrictModel):
    source_path: str = Field(min_length=1, max_length=2000)
    relative_path: str = Field(min_length=1, max_length=1000)
    kind: ArtifactKind
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_artifact_id: str | None = Field(default=None, max_length=120)
    role: str = Field(min_length=1, max_length=120)


class StudioResultPacketImport(StrictModel):
    operation_id: str = Field(min_length=1, max_length=120)
    status: ToolOperationStatus
    elapsed_ms: int = Field(ge=0)
    outputs: tuple[StudioResultOutputImport, ...] = ()
    response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    actual_cost: Money | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def result_is_terminal_and_zero_spend(self) -> StudioResultPacketImport:
        terminal = {
            ToolOperationStatus.SUCCEEDED,
            ToolOperationStatus.FAILED,
            ToolOperationStatus.CANCELLED,
            ToolOperationStatus.OUTCOME_UNKNOWN,
        }
        if self.status not in terminal:
            raise ValueError("Studio results require a terminal status")
        if self.status == ToolOperationStatus.SUCCEEDED and not self.outputs:
            raise ValueError("A successful Studio result must declare output artifacts")
        if self.status != ToolOperationStatus.SUCCEEDED and self.outputs:
            raise ValueError("Only successful Studio results may declare outputs")
        return self


class StudioReadinessReport(StrictModel):
    """Outcome of one live Studio readiness probe, recorded by the operator."""

    connected: bool
    account_state: str = Field(min_length=1, max_length=100)
    full_access: bool
    providers: tuple[str, ...] = ()
    studio_version: str | None = Field(default=None, max_length=50)
    production_folder: str | None = Field(default=None, max_length=1000)
    checked_at: str | None = Field(default=None, max_length=50)
    status: CapabilityStatus = CapabilityStatus.WORKING
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ready_reports_must_be_connected(self) -> StudioReadinessReport:
        if self.status in {CapabilityStatus.WORKING, CapabilityStatus.CONFIGURED}:
            if not self.connected:
                raise ValueError("A ready report must record a live connection")
            if not self.full_access:
                raise ValueError("A ready report requires Full access")
        return self
