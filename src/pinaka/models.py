from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from .enums import (
    AgentStatus,
    ApprovalStage,
    ArtifactKind,
    AssetAcquisitionMethod,
    AssetEligibilityStatus,
    AssetMediaType,
    AssetProvider,
    AssetUseClass,
    BudgetCategory,
    BudgetPriority,
    CapabilityStatus,
    ContentMode,
    CreativeElementKind,
    DataClassification,
    DecisionProvenance,
    DecisionRisk,
    DeliverableKind,
    GroundingBundleKind,
    JobStatus,
    ProjectStatus,
    ProviderExecutionMode,
    ProviderIntegrationMode,
    QAStatus,
    StudioAutonomy,
    StudioWorkPhase,
    ToolIntegrationMode,
    ToolOperationStatus,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Money(StrictModel):
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @property
    def minor(self) -> int:
        return int(self.amount * 100)

    @classmethod
    def from_minor(cls, amount_minor: int, currency: str) -> Money:
        return cls(amount=Decimal(amount_minor) / Decimal(100), currency=currency)


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


class DependencyRef(StrictModel):
    entity_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    entity_id: str = Field(min_length=1, max_length=120)
    entity_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ApprovalRequest(StrictModel):
    stage: ApprovalStage
    approved_by: str = Field(min_length=1, max_length=200)
    scope: dict[str, Any] = Field(default_factory=dict)
    output_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    dependencies: tuple[DependencyRef, ...] = ()


class ArtifactCreate(StrictModel):
    kind: ArtifactKind
    relative_path: str = Field(min_length=1, max_length=1000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_artifact_id: str | None = None
    operation: dict[str, Any] = Field(default_factory=dict)
    immutable: bool = False
    dependencies: tuple[DependencyRef, ...] = ()


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


class QACheck(StrictModel):
    check: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,80}$")
    status: QAStatus
    detail: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class QAReport(StrictModel):
    schema_version: int = 1
    project_id: str = Field(min_length=1, max_length=120)
    subject_artifact_id: str = Field(min_length=1, max_length=120)
    checks: tuple[QACheck, ...] = Field(min_length=1)
    overall: QAStatus
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def overall_matches_checks(self) -> QAReport:
        statuses = {check.status for check in self.checks}
        expected = (
            QAStatus.FAIL
            if QAStatus.FAIL in statuses
            else QAStatus.WARN
            if QAStatus.WARN in statuses
            else QAStatus.PASS
        )
        if self.overall != expected:
            raise ValueError(
                f"Report overall {self.overall.value!r} does not match checks {expected.value!r}"
            )
        return self


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


class TextBriefRequest(StrictModel):
    text: str = Field(min_length=1, max_length=100000)
    audience: str | None = Field(default=None, max_length=500)
    language: str | None = Field(default=None, min_length=2, max_length=20)
    output_format: str | None = Field(default=None, max_length=50)
    destination: str | None = Field(default=None, max_length=100)
    duration_seconds: Decimal | None = Field(default=None, gt=0, le=300)
    create_plan: bool = True
    persist_elements: bool = False


class BudgetRaiseCreate(StrictModel):
    new_max: Money
    raised_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class OverageReconcileCreate(StrictModel):
    job_id: str = Field(min_length=1, max_length=120)
    actual_cost: Money
    reconciled_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class PublicationAuthorizationCreate(StrictModel):
    channels: tuple[str, ...] = Field(min_length=1)
    authorized_by: str = Field(min_length=1, max_length=200)
    reference: str | None = Field(default=None, max_length=500)
    expires_at: str | None = Field(default=None, max_length=50)


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


class AssetSourceRecord(StrictModel):
    provider: AssetProvider
    provider_asset_id: str = Field(min_length=1, max_length=200)
    media_type: AssetMediaType
    acquisition_method: AssetAcquisitionMethod
    source_page_url: HttpUrl | None = None
    creator_name: str = Field(min_length=1, max_length=300)
    creator_url: HttpUrl | None = None
    direct_asset_url: HttpUrl | None = None
    api_response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AssetRightsRecord(StrictModel):
    license_name: str = Field(min_length=1, max_length=300)
    license_code: str | None = Field(default=None, max_length=100)
    license_url: HttpUrl
    terms_url: HttpUrl
    terms_reviewed_at: date
    commercial_use_allowed: bool | None = None
    modifications_allowed: bool | None = None
    editorial_only: bool = False
    attribution_required: bool | None = None
    attribution_text: str | None = Field(default=None, max_length=2000)
    license_evidence_ref: str | None = Field(default=None, max_length=1000)
    license_evidence_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    origin_license_verified: bool = False
    contains_recognizable_people_or_brands: bool = False
    third_party_rights_cleared: bool | None = None
    rights_uncertainty: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()

    @field_validator("terms_reviewed_at")
    @classmethod
    def terms_date_cannot_be_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("terms_reviewed_at cannot be in the future")
        return value


class ProviderComplianceRecord(StrictModel):
    human_selected: bool = True
    hotlink_preview_used: bool | None = None
    download_event_recorded: bool | None = None
    provider_credit_planned: bool | None = None
    creator_credit_planned: bool | None = None
    openverse_disclosure_planned: bool | None = None


class AssetCandidate(StrictModel):
    source: AssetSourceRecord
    rights: AssetRightsRecord
    compliance: ProviderComplianceRecord = Field(
        default_factory=ProviderComplianceRecord
    )


class AssetUseContext(StrictModel):
    use_class: AssetUseClass
    monetized: bool = False
    advertising: bool = False
    modification_planned: bool = True
    attribution_can_be_delivered: bool = True
    standalone_redistribution: bool = False
    implies_endorsement: bool = False

    @property
    def requires_commercial_rights(self) -> bool:
        return self.use_class == AssetUseClass.COMMERCIAL or any(
            (self.monetized, self.advertising)
        )


class ProviderProfile(StrictModel):
    provider: AssetProvider
    media_types: tuple[AssetMediaType, ...]
    acquisition_methods: tuple[AssetAcquisitionMethod, ...]
    integration_mode: ProviderIntegrationMode
    official_urls: tuple[HttpUrl, ...] = ()
    terms_reviewed_at: date
    notes: tuple[str, ...] = ()


class AssetEligibilityResult(StrictModel):
    status: AssetEligibilityStatus
    blocking_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.status == AssetEligibilityStatus.ELIGIBLE
