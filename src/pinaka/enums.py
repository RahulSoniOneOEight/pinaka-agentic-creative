from enum import Enum


class ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ContentMode(ValueEnum):
    PRODUCT = "product"
    ENTERTAINMENT = "entertainment"
    STILL = "still"


class ProjectStatus(ValueEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    BRIEF_APPROVED = "brief_approved"
    FACTS_LOCKED = "facts_locked"
    SOURCING = "sourcing"
    ANIMATIC_READY = "animatic_ready"
    ANIMATIC_APPROVED = "animatic_approved"
    FINAL_CANDIDATE = "final_candidate"
    FINAL_APPROVED = "final_approved"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class ApprovalStage(ValueEnum):
    BRIEF = "brief"
    SOURCING = "sourcing"
    ANIMATIC = "animatic"
    FINAL = "final"


class ApprovalStatus(ValueEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"


class AgentStatus(ValueEnum):
    COMPLETED = "completed"
    NEEDS_REVISION = "needs_revision"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    FAILED = "failed"


class JobStatus(ValueEnum):
    PLANNED = "planned"
    BUDGET_RESERVED = "budget_reserved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUBMISSION_UNKNOWN = "submission_unknown"


class ArtifactKind(ValueEnum):
    ORIGINAL = "original"
    DERIVATIVE = "derivative"
    GENERATED = "generated"
    DESIGN = "design"
    AUDIO = "audio"
    PREVIEW = "preview"
    RENDER = "render"
    QA = "qa"


class ArtifactStatus(ValueEnum):
    CURRENT = "current"
    STALE = "stale"


class CapabilityStatus(ValueEnum):
    CONFIGURED = "configured"
    WORKING = "working"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class BudgetEntryKind(ValueEnum):
    RESERVE = "reserve"
    RELEASE = "release"
    CHARGE = "charge"


class AssetProvider(ValueEnum):
    PEXELS = "pexels"
    OPENVERSE = "openverse"
    UNSPLASH = "unsplash"
    MIXKIT = "mixkit"
    VIDEEZY = "videezy"


class AssetMediaType(ValueEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEMPLATE = "template"


class AssetAcquisitionMethod(ValueEnum):
    OFFICIAL_API = "official_api"
    MANUAL_IMPORT = "manual_import"


class AssetUseClass(ValueEnum):
    COMMERCIAL = "commercial"
    NONCOMMERCIAL = "noncommercial"
    EDITORIAL = "editorial"


class AssetEligibilityStatus(ValueEnum):
    ELIGIBLE = "eligible"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


class ProviderIntegrationMode(ValueEnum):
    API_SPIKE_ALLOWED = "api_spike_allowed"
    EXISTING_KEY_REQUIRED = "existing_key_required"
    MANUAL_ONLY = "manual_only"
