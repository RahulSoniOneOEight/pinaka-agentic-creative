from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    Field,
    model_validator,
)

from ..enums import (
    AgentStatus,
    ApprovalStage,
    CapabilityStatus,
    DataClassification,
    JobStatus,
    ToolIntegrationMode,
    ToolOperationStatus,
)
from .common import Money, StrictModel


class JobRequest(StrictModel):
    provider: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=500)
    operation: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}$")
    estimated_cost: Money
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_refs: tuple[str, ...] = ()
    required_approval_stage: ApprovalStage | None = None


class JobRecord(StrictModel):
    id: str
    project_id: str
    provider: str
    endpoint: str
    operation: str
    idempotency_key: str
    status: JobStatus
    estimated_cost: Money
    reserved_cost: Money
    actual_cost: Money
    provider_job_id: str | None
    request_hash: str
    created_at: str
    updated_at: str


class AgentResult(StrictModel):
    status: AgentStatus
    proposed_decision: str | None = None
    artifact_refs: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    estimated_cost: Money | None = None
    actual_cost: Money | None = None
    next_action: str = Field(min_length=1, max_length=1000)


class CapabilityResult(StrictModel):
    name: str
    status: CapabilityStatus
    detail: str
    checked_at: str


class ProviderModelProfileCreate(StrictModel):
    stable_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    provider: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=300)
    approved_roles: tuple[str, ...] = Field(min_length=1)
    credential_ref: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    data_classification: DataClassification = DataClassification.PUBLIC
    limits: dict[str, Any] = Field(default_factory=dict)
    terms: dict[str, Any] = Field(default_factory=dict)
    fallback_profiles: tuple[str, ...] = ()
    enabled: bool = False


class ToolProfileCreate(StrictModel):
    stable_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    name: str = Field(min_length=1, max_length=200)
    integration_mode: ToolIntegrationMode
    executable_or_endpoint: str = Field(min_length=1, max_length=1000)
    supported_operations: tuple[str, ...] = Field(min_length=1)
    limits: dict[str, Any] = Field(default_factory=dict)
    fallback_order: tuple[str, ...] = ()
    capability_status: CapabilityStatus = CapabilityStatus.UNAVAILABLE


class ToolOperationRequest(StrictModel):
    tool_profile_id: str = Field(min_length=1, max_length=120)
    provider_profile_id: str | None = Field(default=None, max_length=120)
    operation: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}$")
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_artifact_ids: tuple[str, ...] = ()
    output_declaration: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    required_approval_stage: ApprovalStage | None = None


class ExecutionReceiptCreate(StrictModel):
    status: ToolOperationStatus
    elapsed_ms: int = Field(ge=0)
    output_artifact_ids: tuple[str, ...] = ()
    response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    actual_cost: Money | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def receipt_must_be_terminal(self) -> ExecutionReceiptCreate:
        if self.status not in {
            ToolOperationStatus.SUCCEEDED,
            ToolOperationStatus.FAILED,
            ToolOperationStatus.CANCELLED,
            ToolOperationStatus.OUTCOME_UNKNOWN,
        }:
            raise ValueError("Execution receipts require a terminal operation status")
        return self


class ShotMotionRequest(StrictModel):
    shot_id: str = Field(min_length=1, max_length=120)
    route: str = Field(default="fal.video", min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    mode: Literal[
        "text-to-video",
        "first-frame",
        "first-and-last-frame",
        "reference-media",
        "video-to-video",
        "audio-to-video",
    ]
    resolution: str = Field(min_length=1, max_length=20)
    duration_seconds: Decimal = Field(gt=0, le=60)
    prompt: str = Field(min_length=1, max_length=4000)
    first_frame_artifact_id: str | None = Field(default=None, max_length=120)
    last_frame_artifact_id: str | None = Field(default=None, max_length=120)
    plan_id: str | None = Field(default=None, max_length=120)
    item_key: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def frames_match_the_mode(self) -> ShotMotionRequest:
        if (
            self.mode in {"first-frame", "first-and-last-frame"}
            and not self.first_frame_artifact_id
        ):
            raise ValueError(f"{self.mode} requires first_frame_artifact_id")
        if self.mode == "first-and-last-frame" and not self.last_frame_artifact_id:
            raise ValueError("first-and-last-frame requires last_frame_artifact_id")
        if self.route != "fal.video":
            raise ValueError(
                "Shot motion runs only through Studio's fal.video route; "
                "Veo and Runway are not part of the flow"
            )
        if "veo" in self.model.lower():
            raise ValueError("Veo models are not part of the flow")
        return self


class AudioCueRequest(StrictModel):
    """One SFX, ambience or stinger placed on the final audio timeline."""

    cue_key: str = Field(min_length=1, max_length=120)
    artifact_id: str = Field(min_length=1, max_length=120)
    role: Literal["sfx", "ambience", "stinger"]
    start_ms: int = Field(ge=0, le=3_600_000)
    duration_ms: int = Field(gt=0, le=10_000)
    gain_db: Decimal = Field(default=Decimal(-18), ge=-60, le=0)
    loop: bool = False
    shot_id: str | None = Field(default=None, max_length=120)
    decided_by: str = Field(min_length=1, max_length=200)
