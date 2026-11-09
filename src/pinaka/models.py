from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

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
    CapabilityStatus,
    ContentMode,
    JobStatus,
    ProjectStatus,
    ProviderIntegrationMode,
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


class ProjectRecord(StrictModel):
    id: str
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


class PilotContract(StrictModel):
    project: ProjectCreate
    approved_fact_source: str | None = None
    rights_policy: str | None = None
    llm_model: str | None = None
    generation_endpoint: str | None = None
    voice_provider: str | None = None
    acceptance_targets: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def product_mode_requires_fact_source_for_lock(self) -> PilotContract:
        # The template may remain unresolved; the controller blocks fact-dependent work later.
        return self


class AssetSourceRecord(StrictModel):
    provider: AssetProvider
    provider_asset_id: str = Field(min_length=1, max_length=200)
    media_type: AssetMediaType
    acquisition_method: AssetAcquisitionMethod
    source_page_url: HttpUrl
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
    official_urls: tuple[HttpUrl, ...]
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
