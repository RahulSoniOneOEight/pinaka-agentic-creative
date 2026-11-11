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
    GROUNDING_LOCKED = "grounding_locked"
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
    CONTRACT = "contract"
    ORIGINAL = "original"
    DERIVATIVE = "derivative"
    GENERATED = "generated"
    DESIGN = "design"
    AUDIO = "audio"
    PREVIEW = "preview"
    RENDER = "render"
    QA = "qa"
    ARCHIVE = "archive"
    EXTERNAL_SPEND = "external_spend"
    REFERENCE = "reference"


class TreatmentVerdict(ValueEnum):
    USE_AS_IS = "use_as_is"
    CORRECT = "correct"
    COMPOSITE = "composite"
    REGENERATE = "regenerate"
    REPLACE = "replace"
    REJECT = "reject"


class SlotAssetRole(ValueEnum):
    PRIMARY = "primary"
    BACKUP = "backup"
    VARIANT_A = "variant_a"
    VARIANT_B = "variant_b"


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
    OWNED = "owned"
    PEXELS = "pexels"
    OPENVERSE = "openverse"
    UNSPLASH = "unsplash"
    MIXKIT = "mixkit"
    VIDEEZY = "videezy"
    WIKIMEDIA = "wikimedia"
    INTERNET_ARCHIVE = "internet_archive"
    FREESOUND = "freesound"


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


class DeliverableKind(ValueEnum):
    VIDEO = "video"
    STILL = "still"
    CAROUSEL = "carousel"
    AUDIO = "audio"
    DOCUMENT = "document"


class GroundingBundleKind(ValueEnum):
    PRODUCT_FACTS = "product_facts"
    CLAIMS = "claims"
    TUTORIAL = "tutorial"
    SOURCES = "sources"
    STORY = "story"
    SERIES = "series"
    BRAND = "brand"
    PLATFORM = "platform"


class DataClassification(ValueEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ToolIntegrationMode(ValueEnum):
    NATIVE = "native"
    MCP = "mcp"
    HEADLESS = "headless"
    SPECIALIST_HANDOFF = "specialist_handoff"
    MANAGED_SERVICE = "managed_service"


class ToolOperationStatus(ValueEnum):
    AUTHORIZED = "authorized"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class DecisionProvenance(ValueEnum):
    SUPPLIED = "supplied"
    DERIVED = "derived"
    DEFAULTED = "defaulted"
    ASSUMED = "assumed"
    APPROVED = "approved"
    BLOCKED = "blocked"


class DecisionRisk(ValueEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CreativeElementKind(ValueEnum):
    CHARACTER = "character"
    PROP = "prop"
    LOCATION = "location"
    WORLD = "world"
    PALETTE = "palette"
    VOICE = "voice"
    STYLE = "style"
    OTHER = "other"


class StudioAutonomy(ValueEnum):
    STANDARD = "standard"
    AUTOPILOT = "autopilot"


class StudioWorkPhase(ValueEnum):
    BRIEF = "brief"
    SCRIPT = "script"
    SCENE_PLAN = "scene_plan"
    STORYBOARD = "storyboard"
    COMPOSITION = "composition"
    NARRATION = "narration"
    MUSIC = "music"
    CAPTIONS = "captions"
    RENDER = "render"
    REVIEW = "review"


class ProviderExecutionMode(ValueEnum):
    DISABLED = "disabled"
    DETERMINISTIC_FAKE = "deterministic_fake"
    CONTROLLED_LIVE = "controlled_live"


class QAStatus(ValueEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
