from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from .adapters.animatic import DeterministicAnimaticAdapter
from .adapters.base import GenerationAdapter
from .adapters.burned_captions import BurnedCaptionAdapter
from .adapters.captions import CaptionMuxAdapter
from .adapters.encode import FFmpegEncodeAdapter, VideoEncodeAdapter
from .adapters.frames import ChromeFrameCapture, FrameCaptureAdapter
from .adapters.google_vertex_image import (
    GoogleVertexImageAdapter,
    estimate_cost_minor,
)
from .adapters.mux import FFmpegMuxAdapter, MuxAdapter
from .adapters.narration import KokoroNarrationAdapter, NarrationAdapter
from .adapters.tool import ToolAdapter
from .ai_edit import ComfyUIEditAdapter
from .analysis import AssetAnalyzer
from .animatic import build_timeline, decimal_seconds
from .artifacts import ArtifactStore
from .assets import (
    AssetCriteria,
    evaluate_treatment,
    verdict_binds_asset,
    verdict_modifies_bytes,
)
from .budget import TieredBudget, evaluate_spend, spend_summary
from .capabilities import probe_all
from .captions import build_srt
from .composition import (
    build_composition,
    composition_duration,
    validate_composition,
)
from .correction import FFmpegCompositeAdapter, FFmpegCorrectionAdapter
from .database import Database
from .enums import (
    ApprovalStage,
    ArtifactKind,
    ArtifactStatus,
    AssetEligibilityStatus,
    AssetUseClass,
    BudgetItemStatus,
    BudgetPlanStatus,
    CapabilityStatus,
    GroundingBundleKind,
    JobStatus,
    ProjectStatus,
    QAStatus,
    SlotAssetRole,
    ToolIntegrationMode,
    ToolOperationStatus,
    TreatmentVerdict,
)
from .errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConfigurationError,
    ConflictError,
    IntegrityError,
    InvalidTransitionError,
    NotFoundError,
    PinakaError,
    QuotaExceededError,
    RightsEligibilityError,
    VarianceApprovalRequiredError,
)
from .evidence import capture_document
from .judge import OpenAICompatibleJudge
from .models import (
    ApprovalRequest,
    ArtifactCreate,
    AssetCandidate,
    AssetEligibilityResult,
    AssetRightsRecord,
    AssetUseContext,
    BudgetPlanApprovalCreate,
    BudgetPlanCreate,
    BudgetRaiseCreate,
    CapabilityResult,
    CreativeElementCreate,
    DeliverableCreate,
    DeliverableRecord,
    DependencyRef,
    ExecutionReceiptCreate,
    FactSetCreate,
    GroundingBundleCreate,
    JobRecord,
    JobRequest,
    Money,
    OverageReconcileCreate,
    PilotContract,
    ProjectCreate,
    ProjectRecord,
    ProviderModelProfileCreate,
    PublicationAuthorizationCreate,
    SegmentCreate,
    ShotCreate,
    ShotElementBindingCreate,
    ShotReferenceCreate,
    SlotCreate,
    StudioReadinessReport,
    StudioResultOutputImport,
    StudioResultPacketImport,
    StudioScope,
    StudioSpendApprovalCreate,
    StudioWorkPacketRequest,
    TextBriefRequest,
    ToolOperationRequest,
    ToolProfileCreate,
    VarianceDecisionCreate,
    WorkableBriefCreate,
)
from .planning import (
    DEFAULT_DURATION_SECONDS,
    analyze_text,
    build_brief,
    propose_elements,
    propose_shots,
    scene_plan_markdown,
)
from .publication import PublicationAuthorization
from .qa import build_report, run_animatic_qa, run_voiced_animatic_qa
from .references import (
    SUPPORTED_PROVIDERS,
    provider_reference_payload,
    reference_summary,
)
from .repository import Repository
from .review_policy import CampaignRisk, ReviewPolicy, decide_review
from .sourcing import clear_resolved_uncertainty, evaluate_asset_candidate
from .studio import StudioAdapter
from .studio_gateway import (
    StudioOpenRequest,
    StudioPending,
    StudioSubmission,
    StudioTransport,
)
from .utils import canonical_json, sha256_bytes, sha256_file, sha256_json, utc_now
from .vertex import (
    DEFAULT_LOCATION,
    DEFAULT_PROJECT,
    TTS_MODEL,
    VertexTTSAdapter,
    estimate_tts_cost_minor,
    verify_models,
    vertex_provider_profile,
)

ALLOWED_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.DRAFT: {ProjectStatus.PLANNED, ProjectStatus.CANCELLED},
    ProjectStatus.PLANNED: {
        ProjectStatus.DRAFT,
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.BRIEF_APPROVED: {
        ProjectStatus.PLANNED,
        ProjectStatus.FACTS_LOCKED,
        ProjectStatus.GROUNDING_LOCKED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FACTS_LOCKED: {
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.GROUNDING_LOCKED,
        ProjectStatus.SOURCING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.GROUNDING_LOCKED: {
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.FACTS_LOCKED,
        ProjectStatus.SOURCING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.SOURCING: {
        ProjectStatus.FACTS_LOCKED,
        ProjectStatus.GROUNDING_LOCKED,
        ProjectStatus.ANIMATIC_READY,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ANIMATIC_READY: {
        ProjectStatus.SOURCING,
        ProjectStatus.ANIMATIC_APPROVED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ANIMATIC_APPROVED: {
        ProjectStatus.SOURCING,
        ProjectStatus.FINAL_CANDIDATE,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FINAL_CANDIDATE: {
        ProjectStatus.ANIMATIC_APPROVED,
        ProjectStatus.FINAL_APPROVED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FINAL_APPROVED: {
        ProjectStatus.FINAL_CANDIDATE,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ARCHIVED: set(),
    ProjectStatus.CANCELLED: set(),
}

APPROVAL_PREDECESSOR: dict[ApprovalStage, ApprovalStage | None] = {
    ApprovalStage.BRIEF: None,
    ApprovalStage.SOURCING: ApprovalStage.BRIEF,
    ApprovalStage.ANIMATIC: ApprovalStage.SOURCING,
    ApprovalStage.FINAL: ApprovalStage.ANIMATIC,
}

APPROVAL_REQUIRED_STATUS: dict[ApprovalStage, ProjectStatus] = {
    ApprovalStage.BRIEF: ProjectStatus.PLANNED,
    ApprovalStage.SOURCING: ProjectStatus.SOURCING,
    ApprovalStage.ANIMATIC: ProjectStatus.ANIMATIC_READY,
    ApprovalStage.FINAL: ProjectStatus.FINAL_CANDIDATE,
}

APPROVAL_RESULT_STATUS: dict[ApprovalStage, ProjectStatus | None] = {
    ApprovalStage.BRIEF: ProjectStatus.BRIEF_APPROVED,
    ApprovalStage.SOURCING: None,
    ApprovalStage.ANIMATIC: ProjectStatus.ANIMATIC_APPROVED,
    ApprovalStage.FINAL: ProjectStatus.FINAL_APPROVED,
}

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "password",
    "secret",
    "authorization",
    "access_token",
)

STUDIO_AUTO_SPEND_MINOR = 100
SPEND_CURRENCIES = ("USD", "INR")
DEFAULT_RUN_CAP_MINOR = {"USD": 100, "INR": 8500}
DEFAULT_CREATIVE_TARGET_MINOR = {"USD": 100, "INR": 8500}
GENERATED_SOURCES = frozenset(
    {"generated", "paid_generation", "ai_generated", "ai", "synthesis"}
)
STUDIO_CAPABILITY_NAME = "openmontage.studio"
STUDIO_READINESS_TTL_SECONDS = 12 * 60 * 60
MAX_LAYER_BYTES = 1_500_000
MAX_EMBEDDED_BYTES = 4_000_000
IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _image_mime(path: Any) -> str | None:
    return IMAGE_MIME.get(str(getattr(path, "suffix", "")).lower())


SUBJECT_ROLE_HINTS = ("subject", "character", "prop", "visual", "hero", "presenter")
BACKGROUND_ROLE_HINTS = ("background", "world", "location", "environment", "scene")


def _slot_accepts_element(slot_role: str, element_kind: str) -> bool:
    role = (slot_role or "").lower()
    kind = (element_kind or "").lower()
    if kind in {"character", "prop"}:
        return any(hint in role for hint in SUBJECT_ROLE_HINTS)
    if kind in {"location", "world"}:
        return any(hint in role for hint in BACKGROUND_ROLE_HINTS)
    return False


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


CAPTION_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _caption_coverage(srt_text: str, duration: float) -> tuple[float, int, bool]:
    """Seconds covered by cues, cue count, and whether a cue overruns."""

    covered = 0.0
    cues = 0
    overrun = False
    for match in CAPTION_TIME.finditer(srt_text):
        groups = [int(value) for value in match.groups()]
        start = groups[0] * 3600 + groups[1] * 60 + groups[2] + groups[3] / 1000
        end = groups[4] * 3600 + groups[5] * 60 + groups[6] + groups[7] / 1000
        cues += 1
        if duration > 0 and end > duration + 0.05:
            overrun = True
        covered += max(0.0, min(end, duration) - min(start, duration))
    return min(covered, duration), cues, overrun


class CreativeController:
    """Policy boundary for state, approvals, spending, and artifact lineage."""

    def __init__(self, database: Database, artifact_store: ArtifactStore):
        self.database = database
        self.database.initialize()
        self.repository = Repository(database)
        self.artifact_store = artifact_store

    @classmethod
    def open(cls, home: Path | str) -> CreativeController:
        root = Path(home).expanduser().resolve()
        return cls(
            database=Database(root / "state" / "pinaka.sqlite"),
            artifact_store=ArtifactStore(root),
        )

    def create_project(self, request: ProjectCreate) -> ProjectRecord:
        project = self.repository.create_project(request)
        self.artifact_store.ensure_project_layout(project.id)
        return project

    def create_revision(
        self,
        parent_project_id: str,
        *,
        name: str,
        objective: str,
        audience: str | None = None,
        output_format: str | None = None,
        language: str | None = None,
        duration_seconds: Any | None = None,
        max_budget: Money | None = None,
    ) -> ProjectRecord:
        """Create a child revision whose parent archive stays untouched."""

        parent = self.repository.get_project(parent_project_id)
        if parent.status not in {ProjectStatus.FINAL_APPROVED, ProjectStatus.ARCHIVED}:
            raise InvalidTransitionError(
                "A revision requires an archived or finally approved parent"
            )
        request = ProjectCreate(
            name=name,
            objective=objective,
            mode=parent.mode,
            audience=audience or parent.audience,
            output_format=output_format or parent.output_format,
            language=language or parent.language,
            duration_seconds=duration_seconds or parent.duration_seconds,
            max_budget=max_budget or parent.max_budget,
            parent_project_id=parent_project_id,
        )
        child = self.create_project(request)
        return child

    def revision_lineage(self, project_id: str) -> list[str]:
        chain: list[str] = []
        current = project_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            current = self.repository.get_project(current).parent_project_id
        return chain

    def record_scene_plan(self, project_id: str, markdown: str) -> dict[str, Any]:
        """Store the SCENE_PLAN document and its hash for review."""

        self.repository.get_project(project_id)
        content = markdown.encode("utf-8")
        content_hash = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"inputs/SCENE_PLAN-{content_hash[:12]}.md",
            content=content,
            operation={"type": "scene_plan"},
        )
        plan = self.repository.record_scene_plan(
            project_id, artifact["id"], content_hash
        )
        return {
            "artifact_id": artifact["id"],
            "relative_path": artifact["relative_path"],
            "content_hash": content_hash,
            "plan": plan,
        }

    def scene_plan(self, project_id: str) -> dict[str, Any] | None:
        plan = self.repository.latest_scene_plan(project_id)
        if not plan:
            return None
        text = self.read_artifact_text(plan["artifact_id"])
        return {**plan, "markdown": text}

    def record_workable_brief(
        self, project_id: str, request: WorkableBriefCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot change the workable brief for a {project.status.value} project"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "workable_brief"
        )
        return self.repository.record_workable_brief(project_id, request)

    def workable_brief(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.latest_workable_brief(project_id)

    def plan_from_text(
        self, project_id: str, request: TextBriefRequest
    ) -> dict[str, Any]:
        """Preserve raw text and derive a governed workable brief from it."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(f"Cannot plan a {project.status.value} project")
        self._reject_sensitive_payload({"text": request.text}, "text_brief")
        content = request.text.encode("utf-8")
        digest = sha256_bytes(content)
        stored = self.artifact_store.write_bytes(
            project_id, f"inputs/brief-source-{digest[:12]}.txt", content
        )
        artifact = self.repository.artifact_by_path(project_id, stored.relative_path)
        if artifact is None:
            artifact = self.repository.register_artifact(
                project_id,
                ArtifactCreate(
                    kind=ArtifactKind.ORIGINAL,
                    relative_path=stored.relative_path,
                    content_hash=stored.content_hash,
                    operation={
                        "type": "text_brief_source",
                        "size_bytes": stored.size_bytes,
                    },
                    immutable=True,
                ),
            )
        analysis = analyze_text(request)
        duration = analysis["duration_seconds"] or DEFAULT_DURATION_SECONDS
        brief_request = build_brief(artifact, analysis, project)
        brief = self.record_workable_brief(project_id, brief_request)
        shots: list[dict[str, Any]] = []
        scene_plan = None
        skipped_reason = None
        if request.create_plan:
            if self.repository.list_shots(project_id):
                skipped_reason = "project already has shots; plan not recreated"
            else:
                for spec in propose_shots(analysis, brief_request.beats):
                    shots.append(self.add_shot(project_id, ShotCreate(**spec)))
                scene_plan = self.record_scene_plan(
                    project_id, scene_plan_markdown(shots, duration)
                )
        proposed_slots = [
            {
                "shot_id": shot["id"],
                "stable_key": f"{shot['stable_key']}.visual",
                "role": "primary visual",
                "identity_requirement": "Original, non-protected subject",
                "fact_requirement": "Matches the planned shot requirement",
                "transformation_permission": "Composition and gentle animation",
                "approved_sources": ["owned", "stock"],
            }
            for shot in shots
        ]
        proposed_elements = propose_elements(request.text)
        recorded_elements: list[dict[str, Any]] = []
        if request.persist_elements:
            for spec in proposed_elements:
                recorded_elements.append(
                    self.repository.record_creative_element(
                        project_id, CreativeElementCreate(**spec)
                    )
                )
        return {
            "source_artifact": artifact,
            "analysis": {
                **analysis,
                "duration_seconds": str(duration),
            },
            "brief": brief,
            "shots": shots,
            "scene_plan": scene_plan,
            "plan_created": scene_plan is not None,
            "plan_skipped_reason": skipped_reason,
            "proposed_slots": proposed_slots,
            "proposed_elements": proposed_elements,
            "recorded_elements": recorded_elements,
        }

    def record_creative_element(
        self, project_id: str, request: CreativeElementCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot change creative elements for a {project.status.value} project"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "creative_element"
        )
        return self.repository.record_creative_element(project_id, request)

    def list_creative_elements(
        self, project_id: str, *, current_only: bool = True
    ) -> list[dict[str, Any]]:
        return self.repository.list_creative_elements(
            project_id, current_only=current_only
        )

    def bind_element_to_shot(
        self, project_id: str, request: ShotElementBindingCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot bind creative elements for a {project.status.value} project"
            )
        return self.repository.bind_element_to_shot(project_id, request)

    def bind_shot_reference(
        self, project_id: str, request: ShotReferenceCreate
    ) -> dict[str, Any]:
        """Attach a governed reference image to a shot role."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot bind references for a {project.status.value} project"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "shot_reference"
        )
        return self.repository.upsert_shot_reference(project_id, request)

    def shot_references(
        self, project_id: str, *, shot_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.list_shot_references(project_id, shot_id=shot_id)

    def bind_elements_to_slots(self, project_id: str) -> dict[str, Any]:
        """Assign element artwork to matching slots so compositions pick it up."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot bind artwork for a {project.status.value} project"
            )
        elements = {
            element["id"]: element
            for element in self.repository.list_creative_elements(project_id)
        }
        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        bindings = self.repository.list_shot_element_bindings(project_id)
        assigned: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for binding in bindings:
            element = elements.get(binding["element_id"])
            if not element:
                continue
            artifact_id = element.get("source_artifact_id")
            if not artifact_id:
                skipped.append(
                    {
                        "element": element["stable_key"],
                        "reason": "element has no artwork artifact",
                    }
                )
                continue
            for slot in slots.values():
                if slot["shot_id"] != binding["shot_id"]:
                    continue
                if not _slot_accepts_element(slot["role"], element["kind"]):
                    continue
                existing = self.repository.slot_asset_by_role(
                    slot["id"], SlotAssetRole.PRIMARY.value
                )
                if existing:
                    skipped.append(
                        {
                            "element": element["stable_key"],
                            "slot": slot["stable_key"],
                            "reason": "slot already has a primary asset",
                        }
                    )
                    continue
                self.assign_slot_asset(
                    project_id,
                    slot["id"],
                    artifact_id=artifact_id,
                    role=SlotAssetRole.PRIMARY,
                    criteria=AssetCriteria(),
                    decided_by="controller:element-binding",
                )
                assigned.append(
                    {
                        "element": element["stable_key"],
                        "slot": slot["stable_key"],
                        "role": slot["role"],
                        "artifact_id": artifact_id,
                    }
                )
        return {
            "project_id": project_id,
            "assigned": assigned,
            "skipped": skipped,
            "assigned_count": len(assigned),
        }

    def create_project_from_contract(
        self, contract: PilotContract
    ) -> tuple[ProjectRecord, dict[str, Any]]:
        self._reject_sensitive_payload(
            contract.model_dump(mode="json"), "pilot_contract"
        )
        project = self.create_project(contract.project)
        content = canonical_json(contract.model_dump(mode="json")).encode("utf-8")
        stored = self.artifact_store.write_bytes(
            project.id, "inputs/pilot-contract.json", content
        )
        artifact = self.repository.register_artifact(
            project.id,
            ArtifactCreate(
                kind=ArtifactKind.CONTRACT,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation={
                    "type": "pilot_contract",
                    "size_bytes": stored.size_bytes,
                },
                immutable=True,
            ),
        )
        return project, artifact

    def apply_pilot_plan(
        self, project_id: str, contract: PilotContract
    ) -> dict[str, Any]:
        plan = contract.deliverable_plan
        if plan is None:
            raise IntegrityError("Pilot contract does not contain a deliverable plan")
        project = self.repository.get_project(project_id)
        if project.name != contract.project.name:
            raise IntegrityError("Pilot contract does not match the target project")
        deliverable = self.add_deliverable(project_id, plan.deliverable)
        segments = [
            self.add_segment(
                project_id,
                SegmentCreate(
                    deliverable_id=deliverable.id,
                    **segment.model_dump(mode="python"),
                ),
            )
            for segment in plan.segments
        ]
        if project.status == ProjectStatus.DRAFT:
            project = self.transition_project(
                project_id,
                ProjectStatus.PLANNED,
                reason="Validated pilot deliverable and segment plan recorded",
            )
        elif project.status != ProjectStatus.PLANNED:
            project = self.repository.get_project(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "deliverable": deliverable.model_dump(mode="json"),
            "segments": segments,
            "next_action": self.next_action(project_id),
        }

    def ground_pilot_from_contract(
        self, project_id: str, contract: PilotContract
    ) -> list[dict[str, Any]]:
        project = self.repository.get_project(project_id)
        if project.name != contract.project.name:
            raise IntegrityError("Pilot contract does not match the target project")
        if project.status not in {
            ProjectStatus.BRIEF_APPROVED,
            ProjectStatus.GROUNDING_LOCKED,
        }:
            raise ApprovalRequiredError(
                "Brief approval is required before pilot grounding is persisted"
            )
        if not contract.client or not contract.design or not contract.script:
            raise IntegrityError(
                "Pilot contract requires client, design and script data for grounding"
            )
        source_ref = contract.approved_fact_source or str(contract.client.website)
        evidence_refs = (str(contract.client.website),)
        requests = (
            GroundingBundleCreate(
                kind=GroundingBundleKind.BRAND,
                source_ref=source_ref,
                values={
                    "client": contract.client.model_dump(mode="json"),
                    "design": contract.design.model_dump(mode="json"),
                },
                evidence_refs=evidence_refs,
            ),
            GroundingBundleCreate(
                kind=GroundingBundleKind.CLAIMS,
                source_ref=source_ref,
                values={
                    "approved_service_claims": contract.client.approved_service_claims,
                    "voiceover": contract.script.voiceover,
                    "on_screen_text": contract.script.on_screen_text,
                    "prohibited_claims": contract.script.prohibited_claims,
                },
                evidence_refs=evidence_refs,
            ),
            GroundingBundleCreate(
                kind=GroundingBundleKind.SOURCES,
                source_ref=source_ref,
                values={
                    "approved_fact_source": contract.approved_fact_source,
                    "rights_policy": contract.rights_policy,
                    "brand_source_authorized": contract.client.brand_source_authorized,
                },
                evidence_refs=evidence_refs,
            ),
        )
        return [self.add_grounding_bundle(project_id, request) for request in requests]

    def add_deliverable(
        self, project_id: str, request: DeliverableCreate
    ) -> DeliverableRecord:
        return self.repository.create_deliverable(project_id, request)

    def add_segment(self, project_id: str, request: SegmentCreate) -> dict[str, Any]:
        return self.repository.create_segment(project_id, request)

    def add_grounding_bundle(
        self, project_id: str, request: GroundingBundleCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot change grounding for a {project.status.value} project"
            )
        self._reject_sensitive_payload(request.model_dump(mode="json"), "grounding")
        return self.repository.add_grounding_bundle(project_id, request)

    def transition_project(
        self, project_id: str, target: ProjectStatus, *, reason: str
    ) -> ProjectRecord:
        project = self.repository.get_project(project_id)
        if target not in ALLOWED_TRANSITIONS[project.status]:
            raise InvalidTransitionError(
                f"Cannot transition project from {project.status.value} to {target.value}"
            )
        if (
            target == ProjectStatus.FACTS_LOCKED
            and not self.repository.current_fact_set(project_id)
        ):
            raise IntegrityError(
                "A versioned fact set is required before facts can be locked"
            )
        if (
            target == ProjectStatus.GROUNDING_LOCKED
            and not self.repository.current_grounding_bundles(project_id)
        ):
            raise IntegrityError(
                "At least one current grounding bundle is required before grounding can be locked"
            )
        return self.repository.set_project_status(
            project_id,
            target,
            reason=reason,
            expected_status=project.status,
        )

    def add_fact_set(self, project_id: str, request: FactSetCreate) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        blocked = {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}
        if project.status in blocked:
            raise ConflictError(
                f"Cannot change facts for a {project.status.value} project"
            )
        return self.repository.add_fact_set(project_id, request)

    def add_shot(self, project_id: str, request: ShotCreate) -> dict[str, Any]:
        return self.repository.add_shot(project_id, request)

    def add_slot(self, project_id: str, request: SlotCreate) -> dict[str, Any]:
        return self.repository.add_slot(project_id, request)

    def approve(self, project_id: str, request: ApprovalRequest) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        required_status = APPROVAL_REQUIRED_STATUS[request.stage]
        if project.status != required_status:
            raise ApprovalRequiredError(
                f"{request.stage.value} approval requires project status "
                f"{required_status.value}; observed {project.status.value}"
            )
        predecessor_stage = APPROVAL_PREDECESSOR[request.stage]
        dependencies: list[DependencyRef] = list(request.dependencies)
        if predecessor_stage:
            predecessor = self.repository.active_approval(
                project_id, predecessor_stage.value
            )
            if not predecessor:
                raise ApprovalRequiredError(
                    f"Active {predecessor_stage.value} approval is required"
                )
            dependencies.append(
                DependencyRef(
                    entity_type="approval",
                    entity_id=predecessor["id"],
                    entity_hash=predecessor["subject_hash"],
                )
            )
        if request.stage in {
            ApprovalStage.SOURCING,
            ApprovalStage.ANIMATIC,
            ApprovalStage.FINAL,
        }:
            grounding = self.repository.current_grounding_bundles(project_id)
            if grounding:
                dependencies.extend(
                    DependencyRef(
                        entity_type="grounding_bundle",
                        entity_id=bundle["id"],
                        entity_hash=bundle["content_hash"],
                    )
                    for bundle in grounding
                )
            else:
                fact_set = self.repository.current_fact_set(project_id)
                if not fact_set:
                    raise ApprovalRequiredError(
                        "Current grounding or a legacy fact set is required for this approval"
                    )
                dependencies.append(
                    DependencyRef(
                        entity_type="fact_set",
                        entity_id=fact_set["id"],
                        entity_hash=fact_set["content_hash"],
                    )
                )
        if request.stage == ApprovalStage.FINAL and not request.output_hash:
            raise IntegrityError("Final approval must bind to an exact output hash")
        if request.stage == ApprovalStage.FINAL:
            candidate = self.repository.artifact_by_content_hash(
                project_id, request.output_hash or ""
            )
            if not candidate:
                raise ApprovalRequiredError(
                    "Final approval hash does not match a current artifact in this project"
                )
            if not self.verify_artifact(candidate["id"]):
                raise IntegrityError(
                    "Final candidate file does not match its registered hash"
                )
            checks = [
                result
                for result in self.repository.list_qa_results(project_id)
                if result["artifact_id"] == candidate["id"]
            ]
            if not checks:
                raise ApprovalRequiredError(
                    "Final approval requires recorded QA for the exact candidate artifact"
                )
            if any(result["status"] == QAStatus.FAIL.value for result in checks):
                raise ApprovalRequiredError(
                    "Final candidate has failing QA checks and cannot be approved"
                )
            dependencies.append(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=candidate["id"],
                    entity_hash=candidate["content_hash"],
                )
            )

        unique_dependencies = self._deduplicate_dependencies(dependencies)
        subject_hash = sha256_json(
            {
                "project_id": project_id,
                "project_version": project.version,
                "stage": request.stage.value,
                "scope": request.scope,
                "output_hash": request.output_hash,
                "dependencies": [
                    item.model_dump(mode="json") for item in unique_dependencies
                ],
            }
        )
        approval = self.repository.create_approval(
            project_id,
            request,
            unique_dependencies,
            subject_hash,
            expected_project_status=required_status,
            resulting_project_status=APPROVAL_RESULT_STATUS[request.stage],
        )
        return approval

    @staticmethod
    def _deduplicate_dependencies(
        dependencies: Iterable[DependencyRef],
    ) -> tuple[DependencyRef, ...]:
        unique: dict[tuple[str, str], DependencyRef] = {}
        for dependency in dependencies:
            key = (dependency.entity_type, dependency.entity_id)
            existing = unique.get(key)
            if existing and existing.entity_hash != dependency.entity_hash:
                raise IntegrityError(
                    f"Conflicting dependency hashes for {key[0]}:{key[1]}"
                )
            unique[key] = dependency
        return tuple(unique[key] for key in sorted(unique))

    def import_original(self, project_id: str, source: Path | str) -> dict[str, Any]:
        self.repository.get_project(project_id)
        stored = self.artifact_store.import_original(project_id, source)
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if (
                existing["content_hash"] != stored.content_hash
                or not existing["immutable"]
            ):
                raise IntegrityError(
                    f"Original registry mismatch: {stored.relative_path}"
                )
            return existing
        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=ArtifactKind.ORIGINAL,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation={"type": "import", "size_bytes": stored.size_bytes},
                immutable=True,
            ),
        )

    def evaluate_asset_candidate(
        self, candidate: AssetCandidate, context: AssetUseContext
    ) -> AssetEligibilityResult:
        return evaluate_asset_candidate(candidate, context)

    def import_sourced_original(
        self,
        project_id: str,
        source: Path | str,
        *,
        candidate: AssetCandidate,
        context: AssetUseContext,
    ) -> dict[str, Any]:
        """Import only rights-eligible stock and retain its complete source manifest."""

        self.repository.get_project(project_id)
        eligibility = self.evaluate_asset_candidate(candidate, context)
        if eligibility.status != AssetEligibilityStatus.ELIGIBLE:
            reasons = (*eligibility.blocking_reasons, *eligibility.review_reasons)
            raise RightsEligibilityError(
                f"Asset is {eligibility.status.value}: {'; '.join(reasons)}"
            )

        stored = self.artifact_store.import_original(project_id, source)
        manifest = {
            "candidate": candidate.model_dump(mode="json"),
            "use_context": context.model_dump(mode="json"),
        }
        operation = {
            "type": "sourced_import",
            "size_bytes": stored.size_bytes,
            "source": manifest["candidate"]["source"],
            "rights": manifest["candidate"]["rights"],
            "provider_compliance": manifest["candidate"]["compliance"],
            "use_context": manifest["use_context"],
            "eligibility": eligibility.model_dump(mode="json"),
            "source_manifest_hash": sha256_json(manifest),
        }

        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if (
                existing["content_hash"] != stored.content_hash
                or not existing["immutable"]
                or existing["operation"] != operation
            ):
                raise IntegrityError(
                    f"Sourced original provenance mismatch: {stored.relative_path}"
                )
            return existing

        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=ArtifactKind.ORIGINAL,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation=operation,
                immutable=True,
            ),
        )

    def capture_asset_evidence(
        self,
        project_id: str,
        candidate: AssetCandidate,
        *,
        fetcher: Any | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Fetch and store the item and licence pages, then mark the licence verified."""

        self.repository.get_project(project_id)
        provider = candidate.source.provider.value
        asset_key = "".join(
            char if char.isalnum() or char in "-_" else "-"
            for char in candidate.source.provider_asset_id
        )[:60]
        pages = []
        if candidate.source.source_page_url is not None:
            pages.append(("item_page", str(candidate.source.source_page_url)))
        pages.append(("licence_page", str(candidate.rights.license_url)))
        documents: list[dict[str, Any]] = []
        for kind, url in pages:
            document = capture_document(kind, url, fetcher=fetcher, timeout=timeout)
            evidence = document.evidence
            relative = (
                f"references/licence/{provider}-{asset_key}-{kind}-"
                f"{evidence.content_hash[:12]}.txt"
            )
            artifact = self.write_artifact(
                project_id,
                kind=ArtifactKind.REFERENCE,
                relative_path=relative,
                content=document.content,
                operation={
                    "type": "licence_evidence",
                    "kind": kind,
                    "url": url,
                    "content_type": evidence.content_type,
                    "fetched_at": evidence.fetched_at,
                },
            )
            documents.append(
                {
                    "kind": kind,
                    "url": url,
                    "artifact_id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": evidence.content_hash,
                    "content_type": evidence.content_type,
                    "fetched_at": evidence.fetched_at,
                }
            )

        licence = next(item for item in documents if item["kind"] == "licence_page")
        rights = candidate.rights.model_copy(
            update={
                "license_evidence_ref": licence["relative_path"],
                "license_evidence_hash": licence["content_hash"],
                "origin_license_verified": True,
                "rights_uncertainty": clear_resolved_uncertainty(
                    candidate.rights.rights_uncertainty
                ),
            }
        )
        updated = candidate.model_copy(update={"rights": rights})
        return {
            "candidate": updated,
            "evidence": documents,
            "evidence_hash": sha256_json(documents),
        }

    def import_stock_asset(
        self,
        project_id: str,
        *,
        candidate: AssetCandidate,
        context: AssetUseContext | None = None,
        content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        """Register verified stock bytes as an immutable sourced original."""

        self.repository.get_project(project_id)
        context = self._enforce_context(project_id, context)
        if not content:
            raise IntegrityError("Downloaded asset is empty")
        if not candidate.rights.origin_license_verified:
            raise RightsEligibilityError(
                "Licence evidence must be captured before a stock asset is imported"
            )
        eligibility = self.evaluate_asset_candidate(candidate, context)
        if eligibility.status != AssetEligibilityStatus.ELIGIBLE:
            reasons = (*eligibility.blocking_reasons, *eligibility.review_reasons)
            raise RightsEligibilityError(
                f"Asset is {eligibility.status.value}: {'; '.join(reasons)}"
            )

        content_hash = sha256_bytes(content)
        safe_name = (
            "".join(
                char if char.isalnum() or char in "._-" else "-" for char in filename
            ).strip(".-")
            or "asset.bin"
        )
        relative = f"assets/originals/{content_hash[:12]}-{safe_name}"
        stored = self.artifact_store.write_bytes(project_id, relative, content)

        operation = {
            "type": "sourced_download",
            "size_bytes": stored.size_bytes,
            "source": candidate.source.model_dump(mode="json"),
            "rights": candidate.rights.model_dump(mode="json"),
            "provider_compliance": candidate.compliance.model_dump(mode="json"),
            "use_context": context.model_dump(mode="json"),
            "eligibility": eligibility.model_dump(mode="json"),
            "source_manifest_hash": sha256_json(
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "use_context": context.model_dump(mode="json"),
                }
            ),
        }
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if (
                existing["content_hash"] != stored.content_hash
                or not existing["immutable"]
                or existing["operation"] != operation
            ):
                raise IntegrityError(
                    f"Sourced original provenance mismatch: {stored.relative_path}"
                )
            return existing
        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=ArtifactKind.ORIGINAL,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation=operation,
                immutable=True,
            ),
        )

    def download_stock_asset(
        self,
        project_id: str,
        *,
        candidate: AssetCandidate,
        context: AssetUseContext | None = None,
        fetcher: Any | None = None,
        timeout: int = 120,
        max_bytes: int = 80_000_000,
        download_event_url: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Download a verified stock asset and register it with its provider duties."""

        import urllib.request

        self.repository.get_project(project_id)
        asset_url = candidate.source.direct_asset_url
        if asset_url is None:
            raise RightsEligibilityError(
                "The candidate has no direct asset URL to download"
            )

        def _fetch(url: str) -> tuple[bytes, str | None]:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "pinaka-agentic-creative/0.1 (governed import)"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type")
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise IntegrityError(f"Asset exceeds {max_bytes} bytes: {url}")
            return data, content_type

        fetch = fetcher or _fetch
        if download_event_url:
            fetch(download_event_url)
        content, content_type = fetch(str(asset_url))
        if content_type and not content_type.lower().startswith(
            ("image/", "video/", "audio/")
        ):
            raise IntegrityError(f"Unexpected asset content type: {content_type}")
        compliance = candidate.compliance.model_copy(
            update={"download_event_recorded": True}
        )
        updated = candidate.model_copy(update={"compliance": compliance})
        suffix = ""
        if content_type:
            lowered = content_type.lower()
            for marker, ext in (
                ("jpeg", ".jpg"),
                ("png", ".png"),
                ("mp4", ".mp4"),
                ("webm", ".webm"),
                ("mpeg", ".mp3"),
                ("wav", ".wav"),
                ("ogg", ".ogg"),
                ("flac", ".flac"),
            ):
                if marker in lowered:
                    suffix = ext
                    break
        name = filename or f"{candidate.source.provider_asset_id}{suffix or '.bin'}"
        artifact = self.import_stock_asset(
            project_id,
            candidate=updated,
            context=context,
            content=content,
            filename=name,
        )
        return {
            "artifact": artifact,
            "candidate": updated.model_dump(mode="json"),
            "content_type": content_type,
            "size_bytes": len(content),
        }

    def finalize_asset(
        self,
        project_id: str,
        slot_id: str,
        *,
        artifact_id: str,
        criteria: AssetCriteria,
        context: AssetUseContext | None = None,
        decided_by: str,
    ) -> dict[str, Any]:
        """Record how one slot is satisfied: as-is, corrected, replaced or rejected."""

        context = self._enforce_context(project_id, context)
        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")

        rights_payload = artifact["operation"].get("rights")
        rights = (
            AssetRightsRecord.model_validate(rights_payload) if rights_payload else None
        )
        decision = evaluate_treatment(
            slot_id=slot_id,
            criteria=criteria,
            context=context,
            decided_by=decided_by,
            rights=rights,
            artifact_id=artifact_id,
        )
        recorded = self.repository.register_treatment_decision(
            project_id,
            decision.model_dump(mode="json"),
            decision_hash=decision.decision_hash,
            artifact_id=artifact_id if verdict_binds_asset(decision.verdict) else None,
            decided_by=decided_by,
            created_at=decision.decided_at,
        )
        return {
            "decision": recorded,
            "verdict": decision.verdict.value,
            "bound": verdict_binds_asset(decision.verdict),
            "requires_new_artifact": verdict_modifies_bytes(decision.verdict),
            "reasons": list(decision.reasons),
        }

    def assign_slot_asset(
        self,
        project_id: str,
        slot_id: str,
        *,
        artifact_id: str,
        role: SlotAssetRole,
        criteria: AssetCriteria,
        decided_by: str,
        context: AssetUseContext | None = None,
    ) -> dict[str, Any]:
        """Bind one artifact to a slot as primary, backup, or an A/B variant."""

        context = self._enforce_context(project_id, context)
        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")

        rights_payload = artifact["operation"].get("rights")
        rights = (
            AssetRightsRecord.model_validate(rights_payload) if rights_payload else None
        )
        decision = evaluate_treatment(
            slot_id=slot_id,
            criteria=criteria,
            context=context,
            decided_by=decided_by,
            rights=rights,
            artifact_id=artifact_id,
        )
        if not verdict_binds_asset(decision.verdict):
            raise RightsEligibilityError(
                f"Verdict {decision.verdict.value} cannot be assigned to a slot: "
                + "; ".join(decision.reasons)
            )

        if role in {SlotAssetRole.VARIANT_A, SlotAssetRole.VARIANT_B}:
            other = (
                SlotAssetRole.VARIANT_B
                if role == SlotAssetRole.VARIANT_A
                else SlotAssetRole.VARIANT_A
            )
            existing_variant = self.repository.slot_asset_by_role(slot_id, other.value)
            if existing_variant and existing_variant["artifact_id"] == artifact_id:
                raise ConflictError("A and B variants must be different artifacts")

        recorded = self.repository.register_treatment_decision(
            project_id,
            decision.model_dump(mode="json"),
            decision_hash=decision.decision_hash,
            artifact_id=artifact_id,
            decided_by=decided_by,
            created_at=decision.decided_at,
        )
        assignment = self.repository.upsert_slot_asset(
            project_id,
            slot_id,
            artifact_id,
            role.value,
            treatment_decision_id=recorded["id"],
        )
        return {
            "assignment": assignment,
            "decision": recorded,
            "verdict": decision.verdict.value,
            "role": role.value,
        }

    def assign_audio_bed(
        self,
        project_id: str,
        *,
        artifact_id: str,
        role: SlotAssetRole,
        criteria: AssetCriteria,
        decided_by: str,
        context: AssetUseContext | None = None,
    ) -> dict[str, Any]:
        """Bind a sourced track as the primary or backup music bed."""

        context = self._enforce_context(project_id, context)
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        rights_payload = artifact["operation"].get("rights")
        rights = (
            AssetRightsRecord.model_validate(rights_payload) if rights_payload else None
        )
        decision = evaluate_treatment(
            slot_id=f"audio:{role.value}",
            criteria=criteria,
            context=context,
            decided_by=decided_by,
            rights=rights,
            artifact_id=artifact_id,
        )
        if not verdict_binds_asset(decision.verdict):
            raise RightsEligibilityError(
                f"Verdict {decision.verdict.value} cannot be assigned as a music bed: "
                + "; ".join(decision.reasons)
            )
        bed = self.repository.upsert_audio_bed(
            project_id,
            artifact_id,
            role.value,
            verdict=decision.verdict.value,
            decision=decision.model_dump(mode="json"),
        )
        return {"bed": bed, "verdict": decision.verdict.value, "role": role.value}

    def audio_coverage(self, project_id: str) -> dict[str, Any]:
        """Report whether a primary and a backup music bed exist."""

        beds = {bed["role"]: bed for bed in self.repository.list_audio_beds(project_id)}
        problems: list[str] = []
        if "primary" not in beds:
            problems.append("missing primary")
        if "backup" not in beds:
            problems.append("missing backup")
        return {
            "project_id": project_id,
            "primary_artifact_id": beds.get("primary", {}).get("artifact_id"),
            "backup_artifact_id": beds.get("backup", {}).get("artifact_id"),
            "resolved": not problems,
            "problems": problems,
        }

    def promote_backup(
        self,
        project_id: str,
        slot_id: str,
        *,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Swap the backup asset into use when the person asks for it."""

        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        result = self.repository.swap_slot_asset_roles(
            project_id, slot_id, reason=reason
        )
        return {**result, "decided_by": decided_by, "reason": reason}

    def slot_coverage(self, project_id: str) -> dict[str, Any]:
        """Report primary, backup and A/B coverage for every declared slot."""

        slots = self.repository.list_slots(project_id)
        assignments = self.repository.list_slot_assets(project_id)
        by_slot: dict[str, dict[str, dict[str, Any]]] = {}
        for assignment in assignments:
            by_slot.setdefault(assignment["slot_id"], {})[assignment["role"]] = (
                assignment
            )

        rows = []
        resolved = 0
        for slot in slots:
            roles = by_slot.get(slot["id"], {})
            primary = roles.get(SlotAssetRole.PRIMARY.value)
            backup = roles.get(SlotAssetRole.BACKUP.value)
            variant_a = roles.get(SlotAssetRole.VARIANT_A.value)
            variant_b = roles.get(SlotAssetRole.VARIANT_B.value)

            problems: list[str] = []
            if not primary:
                problems.append("missing primary")
            if not backup:
                problems.append("missing backup")
            if bool(variant_a) != bool(variant_b):
                problems.append("incomplete A/B pair")
            if (
                variant_a
                and variant_b
                and variant_a["artifact_id"] == variant_b["artifact_id"]
            ):
                problems.append("A and B are the same artifact")

            is_resolved = not problems
            if is_resolved:
                resolved += 1
            rows.append(
                {
                    "slot_id": slot["id"],
                    "stable_key": slot["stable_key"],
                    "role": slot["role"],
                    "primary_artifact_id": (
                        primary["artifact_id"] if primary else None
                    ),
                    "backup_artifact_id": backup["artifact_id"] if backup else None,
                    "variant_a_artifact_id": (
                        variant_a["artifact_id"] if variant_a else None
                    ),
                    "variant_b_artifact_id": (
                        variant_b["artifact_id"] if variant_b else None
                    ),
                    "problems": problems,
                    "resolved": is_resolved,
                }
            )
        return {
            "project_id": project_id,
            "slots": rows,
            "total": len(rows),
            "resolved": resolved,
            "unresolved": [row for row in rows if not row["resolved"]],
            "coverage": "complete" if resolved == len(rows) else "incomplete",
        }

    def asset_use_context(self, project_id: str) -> AssetUseContext:
        """Return the recorded use context, or a conservative default for the project."""

        project = self.repository.get_project(project_id)
        stored = self.repository.get_asset_use_context(project_id)
        if stored:
            return AssetUseContext(
                use_class=AssetUseClass(stored["use_class"]),
                monetized=stored["monetized"],
                advertising=stored["advertising"],
                modification_planned=stored["modification_planned"],
                attribution_can_be_delivered=stored["attribution_can_be_delivered"],
            )
        return AssetUseContext(
            use_class=AssetUseClass.COMMERCIAL,
            monetized=project.max_budget.minor > 0,
            advertising=False,
            modification_planned=True,
            attribution_can_be_delivered=True,
        )

    def set_asset_use_context(
        self, project_id: str, context: AssetUseContext
    ) -> dict[str, Any]:
        self.repository.get_project(project_id)
        return self.repository.set_asset_use_context(
            project_id,
            {
                "use_class": context.use_class.value,
                "monetized": context.monetized,
                "advertising": context.advertising,
                "modification_planned": context.modification_planned,
                "attribution_can_be_delivered": context.attribution_can_be_delivered,
            },
        )

    def _enforce_context(
        self, project_id: str, context: AssetUseContext | None
    ) -> AssetUseContext:
        baseline = self.asset_use_context(project_id)
        if context is None:
            return baseline
        weaker: list[str] = []
        if baseline.use_class == AssetUseClass.COMMERCIAL and (
            context.use_class != AssetUseClass.COMMERCIAL
        ):
            weaker.append("use class")
        if baseline.monetized and not context.monetized:
            weaker.append("monetized")
        if baseline.advertising and not context.advertising:
            weaker.append("advertising")
        if baseline.modification_planned and not context.modification_planned:
            weaker.append("modification planned")
        if (
            not baseline.attribution_can_be_delivered
            and context.attribution_can_be_delivered
        ):
            weaker.append("attribution deliverability")
        if weaker:
            raise ConflictError(
                "The supplied use context is weaker than the project's recorded "
                f"context: {', '.join(weaker)}"
            )
        return context

    def analyze_asset(
        self,
        project_id: str,
        artifact_id: str,
        *,
        required_width: int | None = None,
        required_height: int | None = None,
        required_duration_seconds: float | None = None,
        analyzer: AssetAnalyzer | None = None,
    ) -> dict[str, Any]:
        """Measure an asset and propose treatment criteria from observations."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        observation = (analyzer or AssetAnalyzer()).analyze(
            path,
            required_width=required_width,
            required_height=required_height,
            required_duration_seconds=required_duration_seconds,
        )
        criteria = observation.proposed_criteria()
        payload = {
            "schema_version": "asset-analysis/v1",
            "artifact_id": artifact_id,
            "artifact_hash": artifact["content_hash"],
            "observation": observation.model_dump(mode="json"),
            "criteria": criteria.model_dump(mode="json"),
        }
        content = canonical_json(payload).encode("utf-8")
        self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=(
                f"qa/asset-analysis-{artifact_id}-{sha256_bytes(content)[:12]}.json"
            ),
            content=content,
            operation={"type": "asset_analysis", "artifact_id": artifact_id},
        )
        return payload

    def correct_asset(
        self,
        project_id: str,
        *,
        slot_id: str,
        artifact_id: str,
        operations: list[dict[str, Any]],
        adapter: FFmpegCorrectionAdapter | None = None,
    ) -> dict[str, Any]:
        """Produce a corrected derivative after a recorded 'correct' decision."""

        decision = self.repository.latest_treatment_for_slot(slot_id)
        if (
            not decision
            or decision["verdict"] != TreatmentVerdict.CORRECT.value
            or decision["artifact_id"] != artifact_id
        ):
            raise ApprovalRequiredError(
                "A recorded 'correct' treatment decision for this artifact is "
                "required before correcting it"
            )
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        result = (adapter or FFmpegCorrectionAdapter()).correct(path, operations)
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(f"assets/derived/{result.content_hash[:12]}-corrected.png"),
            content=result.content,
            operation={
                "type": "correction",
                "operations": list(operations),
                "filter_graph": result.filter_graph,
                "command_hash": result.command_hash,
                "encoder_version": result.encoder_version,
            },
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "command_hash": result.command_hash,
            "filter_graph": result.filter_graph,
        }

    def compose_assets(
        self,
        project_id: str,
        *,
        background_id: str,
        overlay_id: str,
        x: int = 0,
        y: int = 0,
        width: int | None = None,
        height: int | None = None,
        opacity: float = 1.0,
        adapter: FFmpegCompositeAdapter | None = None,
    ) -> dict[str, Any]:
        """Build a composite derivative from a background and an overlay."""

        background = self.repository.get_artifact(background_id)
        overlay = self.repository.get_artifact(overlay_id)
        for artifact in (background, overlay):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        result = (adapter or FFmpegCompositeAdapter()).compose(
            self.artifact_store.resolve(project_id, background["relative_path"]),
            self.artifact_store.resolve(project_id, overlay["relative_path"]),
            x=x,
            y=y,
            width=width,
            height=height,
            opacity=opacity,
        )
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(f"assets/derived/{result.content_hash[:12]}-composite.png"),
            content=result.content,
            operation={
                "type": "composite",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "opacity": opacity,
                "filter_graph": result.filter_graph,
                "command_hash": result.command_hash,
                "encoder_version": result.encoder_version,
                "overlay_artifact_id": overlay_id,
            },
            parent_artifact_id=background_id,
            dependencies=(DependencyRef(entity_type="artifact", entity_id=overlay_id),),
        )
        return {
            "artifact": derivative,
            "background_artifact_id": background_id,
            "overlay_artifact_id": overlay_id,
            "command_hash": result.command_hash,
        }

    def _require_correction_decision(
        self, slot_id: str, artifact_id: str
    ) -> dict[str, Any]:
        decision = self.repository.latest_treatment_for_slot(slot_id)
        if (
            not decision
            or decision["verdict"] != TreatmentVerdict.CORRECT.value
            or decision["artifact_id"] != artifact_id
        ):
            raise ApprovalRequiredError(
                "A recorded 'correct' treatment decision for this artifact is "
                "required before changing its pixels"
            )
        return decision

    def _read_artifact_bytes(self, project_id: str, artifact_id: str) -> bytes:
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        return self.artifact_store.resolve(
            project_id, artifact["relative_path"]
        ).read_bytes()

    def ai_edit_asset(
        self,
        project_id: str,
        *,
        slot_id: str,
        artifact_id: str,
        prompt: str,
        mask: bytes | None = None,
        checkpoint: str | None = None,
        seed: int = 0,
        steps: int = 20,
        denoise: float = 0.55,
        adapter: ComfyUIEditAdapter | None = None,
    ) -> dict[str, Any]:
        """Inpaint or restyle an asset through the local ComfyUI route."""

        self._require_correction_decision(slot_id, artifact_id)
        content = self._read_artifact_bytes(project_id, artifact_id)
        if adapter is None:
            raise ConfigurationError(
                "A ComfyUI edit adapter with an allowlisted checkpoint is required"
            )
        result = adapter.edit(
            content,
            prompt=prompt,
            checkpoint=checkpoint,
            mask=mask,
            seed=seed,
            steps=steps,
            denoise=denoise,
        )
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"assets/derived/{result.content_hash[:12]}-ai-edit.png",
            content=result.content,
            operation=result.operation,
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "workflow_hash": result.workflow_hash,
            "operation": result.operation["type"],
        }

    def generative_upscale_asset(
        self,
        project_id: str,
        *,
        slot_id: str,
        artifact_id: str,
        model_name: str | None = None,
        adapter: ComfyUIEditAdapter | None = None,
    ) -> dict[str, Any]:
        """Upscale an asset with an allowlisted ESRGAN-class model."""

        self._require_correction_decision(slot_id, artifact_id)
        content = self._read_artifact_bytes(project_id, artifact_id)
        if adapter is None:
            raise ConfigurationError(
                "A ComfyUI adapter with an allowlisted upscale model is required"
            )
        result = adapter.upscale(content, model_name=model_name)
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(f"assets/derived/{result.content_hash[:12]}-upscaled.png"),
            content=result.content,
            operation=result.operation,
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "workflow_hash": result.workflow_hash,
            "operation": result.operation["type"],
        }

    def judge_asset(
        self,
        project_id: str,
        artifact_id: str,
        *,
        model: str,
        intent: str,
        policy: ReviewPolicy | None = None,
        technical_ok: bool = True,
        risk: CampaignRisk | None = None,
        judge: OpenAICompatibleJudge | None = None,
    ) -> dict[str, Any]:
        """Judge tone, brand, composition, legibility and safety with a vision model."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        content = path.read_bytes()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "image/png")
        client = judge or OpenAICompatibleJudge(model=model)
        judgement = client.judge(content, intent=intent, mime=mime)
        policy = policy or ReviewPolicy()
        review = decide_review(judgement, policy, technical_ok=technical_ok, risk=risk)
        payload = {
            "schema_version": "visual-judgement/v1",
            "artifact_id": artifact_id,
            "artifact_hash": artifact["content_hash"],
            "intent": intent,
            "judgement": judgement.model_dump(mode="json"),
            "review": review,
        }
        encoded = canonical_json(payload).encode("utf-8")
        report = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=(
                f"qa/visual-judge-{artifact_id}-{sha256_bytes(encoded)[:12]}.json"
            ),
            content=encoded,
            operation={
                "type": "visual_judgement",
                "model": model,
                "artifact_id": artifact_id,
            },
        )
        return {**payload, "report_artifact_id": report["id"]}

    def reserve_provider_spend(
        self,
        provider: str,
        *,
        amount_usd: Any,
        purpose: str,
        budget: TieredBudget | None = None,
    ) -> dict[str, Any]:
        """Charge a paid call to the primary tier, then the bonus tier."""

        budget = budget or TieredBudget.load()
        amount_cents = int((Decimal(str(amount_usd)) * 100).to_integral_value())
        already = self.repository.provider_spend_total_cents(provider)
        decision = evaluate_spend(already, amount_cents, budget)
        if not decision.allowed:
            raise BudgetExceededError(decision.reason)
        record = self.repository.record_provider_spend(
            provider,
            amount_cents,
            decision.tier,
            purpose,
            notifications=decision.notifications,
        )
        return {
            "decision": decision.model_dump(mode="json"),
            "record": record,
            "notifications": list(decision.notifications),
        }

    def provider_spend_summary(
        self, provider: str, *, budget: TieredBudget | None = None
    ) -> dict[str, Any]:
        budget = budget or TieredBudget.load()
        spent = self.repository.provider_spend_total_cents(provider)
        return {
            **spend_summary(spent, budget),
            "entries": self.repository.list_provider_spend(provider),
        }

    def reserve_provider_request(
        self,
        provider: str,
        operation: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> dict[str, Any]:
        """Refuse a provider request once a rolling window is exhausted."""

        cutoff = (
            (datetime.now(timezone.utc) - timedelta(seconds=window_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        used = self.repository.count_provider_requests(provider, cutoff)
        if used >= limit:
            raise QuotaExceededError(
                f"{provider} request quota reached: {used}/{limit} in "
                f"{window_seconds}s; wait for the window to roll over"
            )
        self.repository.record_provider_request(provider, operation)
        return {
            "provider": provider,
            "operation": operation,
            "used": used + 1,
            "limit": limit,
            "window_seconds": window_seconds,
        }

    def write_artifact(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        relative_path: str,
        content: bytes,
        operation: dict[str, Any],
        parent_artifact_id: str | None = None,
        dependencies: tuple[DependencyRef, ...] = (),
    ) -> dict[str, Any]:
        if kind == ArtifactKind.ORIGINAL:
            raise IntegrityError("Use import_original for protected source assets")
        stored = self.artifact_store.write_bytes(project_id, relative_path, content)
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if existing["content_hash"] != stored.content_hash:
                raise IntegrityError(
                    f"Artifact registry mismatch: {stored.relative_path}"
                )
            return existing
        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=kind,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                parent_artifact_id=parent_artifact_id,
                operation={**operation, "size_bytes": stored.size_bytes},
                immutable=False,
                dependencies=dependencies,
            ),
        )

    def verify_artifact(self, artifact_id: str) -> bool:
        artifact = self.repository.get_artifact(artifact_id)
        return self.artifact_store.verify(
            artifact["project_id"], artifact["relative_path"], artifact["content_hash"]
        )

    def read_artifact_text(self, artifact_id: str) -> str:
        artifact = self.repository.get_artifact(artifact_id)
        path = self.artifact_store.resolve(
            artifact["project_id"], artifact["relative_path"]
        )
        if not path.is_file():
            raise NotFoundError(
                f"Artifact file is missing: {artifact['relative_path']}"
            )
        return path.read_text(encoding="utf-8")

    def register_provider_profile(
        self, request: ProviderModelProfileCreate
    ) -> dict[str, Any]:
        if request.enabled:
            raise ApprovalRequiredError(
                "Live provider activation is disabled by the current zero-spend pilot policy"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "provider_profile"
        )
        return self.repository.register_provider_profile(request)

    def register_tool_profile(self, request: ToolProfileCreate) -> dict[str, Any]:
        self._reject_sensitive_payload(request.model_dump(mode="json"), "tool_profile")
        return self.repository.register_tool_profile(request)

    def reserve_tool_operation(
        self, project_id: str, request: ToolOperationRequest
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        self._reject_sensitive_payload(payload, "tool_operation")
        if request.required_approval_stage:
            approval = self.repository.active_approval(
                project_id, request.required_approval_stage.value
            )
            if not approval:
                raise ApprovalRequiredError(
                    f"Active {request.required_approval_stage.value} approval is required"
                )
        return self.repository.reserve_tool_operation(
            project_id, request, sha256_json(payload)
        )

    def record_execution_receipt(
        self, operation_id: str, request: ExecutionReceiptCreate
    ) -> dict[str, Any]:
        self._reject_sensitive_payload(request.model_dump(mode="json"), "receipt")
        return self.repository.record_execution_receipt(operation_id, request)

    @staticmethod
    def _studio_spend_scope_hash(
        project_id: str, request: StudioWorkPacketRequest
    ) -> str:
        payload = request.model_dump(mode="json")
        payload["spend_approval_id"] = None
        return sha256_json({"project_id": project_id, "work_packet": payload})

    @staticmethod
    def _auto_run_cap_minor(currency: str) -> int:
        if currency not in DEFAULT_RUN_CAP_MINOR:
            raise IntegrityError(
                f"Unsupported spending currency {currency!r}; "
                f"supported: {', '.join(SPEND_CURRENCIES)}"
            )
        return DEFAULT_RUN_CAP_MINOR[currency]

    @classmethod
    def _resolve_run_cap(cls, project: ProjectRecord, scope: StudioScope) -> Money:
        currency = project.max_budget.currency
        auto_minor = cls._auto_run_cap_minor(currency)
        if scope.max_amount is None:
            return Money.from_minor(
                auto_minor if scope.allow_paid_generation else 0, currency
            )
        if scope.max_currency != currency:
            raise IntegrityError(
                f"Studio runs must reserve in the project currency {currency}"
            )
        return Money(amount=scope.max_amount, currency=currency)

    def record_studio_readiness(self, report: StudioReadinessReport) -> dict[str, Any]:
        """Record one live Studio readiness probe for this session."""

        self._reject_sensitive_payload(
            report.model_dump(mode="json"), "studio_readiness"
        )
        self.repository.record_capability(
            CapabilityResult(
                name=STUDIO_CAPABILITY_NAME,
                status=report.status,
                detail=json.dumps(report.model_dump(mode="json"), sort_keys=True),
                checked_at=utc_now(),
            )
        )
        return self.studio_status()

    def studio_status(self) -> dict[str, Any]:
        """Report whether Studio has been verified recently enough to use."""

        latest = self.repository.latest_capability(STUDIO_CAPABILITY_NAME)
        if not latest:
            return {
                "ready": False,
                "stale": True,
                "age_seconds": None,
                "ttl_seconds": STUDIO_READINESS_TTL_SECONDS,
                "status": None,
                "checked_at": None,
                "report": None,
                "reason": "Studio readiness has never been checked in this home",
            }
        age = None
        try:
            stamp = datetime.fromisoformat(latest["checked_at"].replace("Z", "+00:00"))
            age = int((datetime.now(timezone.utc) - stamp).total_seconds())
        except (AttributeError, ValueError):
            stamp = None
        stale = age is None or age > STUDIO_READINESS_TTL_SECONDS
        report = json.loads(latest["detail"]) if latest["detail"] else None
        healthy = latest["status"] in {
            CapabilityStatus.WORKING.value,
            CapabilityStatus.CONFIGURED.value,
        }
        ready = healthy and not stale
        return {
            "ready": ready,
            "stale": stale,
            "age_seconds": age,
            "ttl_seconds": STUDIO_READINESS_TTL_SECONDS,
            "status": latest["status"],
            "checked_at": latest["checked_at"],
            "report": report,
            "reason": None
            if ready
            else (
                "The last Studio check is stale; run pinaka studio-check again"
                if stale
                else "Studio reported a non-ready status"
            ),
        }

    def approve_studio_spend(
        self, project_id: str, request: StudioSpendApprovalCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot approve Studio spend for a {project.status.value} project"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "studio_spend_approval"
        )
        maximum = self._resolve_run_cap(project, request.work_packet.scope)
        if maximum.minor <= self._auto_run_cap_minor(maximum.currency):
            raise IntegrityError(
                "A run within the automatic limit does not need spend approval"
            )
        return self.repository.record_studio_spend_approval(
            project_id,
            scope_hash=self._studio_spend_scope_hash(project_id, request.work_packet),
            max_cost=maximum,
            approved_by=request.approved_by,
            reason=request.reason,
        )

    def plan_creative_budget(
        self, project_id: str, request: BudgetPlanCreate
    ) -> dict[str, Any]:
        """Plan one creative against a currency target and decide on approval."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot plan a budget for a {project.status.value} project"
            )
        self._reject_sensitive_payload(request.model_dump(mode="json"), "budget_plan")
        currency = request.currency
        self._auto_run_cap_minor(currency)
        if currency != project.max_budget.currency:
            raise IntegrityError(
                "Creative budget plan currency must match the project budget "
                f"currency {project.max_budget.currency}"
            )
        target_minor = (
            Money(amount=request.target_amount, currency=currency).minor
            if request.target_amount is not None
            else DEFAULT_CREATIVE_TARGET_MINOR[currency]
        )
        planned_minor = request.planned_total_minor
        status = (
            BudgetPlanStatus.WITHIN_TARGET
            if planned_minor <= target_minor
            else BudgetPlanStatus.APPROVAL_REQUIRED
        )
        plan = self.repository.record_budget_plan(
            project_id, request, status=status.value, target_minor=target_minor
        )
        return {
            "plan": plan,
            "target": Money.from_minor(target_minor, currency).model_dump(mode="json"),
            "planned_total": Money.from_minor(planned_minor, currency).model_dump(
                mode="json"
            ),
            "overage": Money.from_minor(
                max(0, planned_minor - target_minor), currency
            ).model_dump(mode="json"),
            "within_target": status == BudgetPlanStatus.WITHIN_TARGET,
            "requires_approval": status == BudgetPlanStatus.APPROVAL_REQUIRED,
            "summary": self.budget_summary(project_id),
        }

    def approve_budget_plan(
        self, project_id: str, request: BudgetPlanApprovalCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot approve a budget for a {project.status.value} project"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "budget_plan_approval"
        )
        plan = self.repository.get_budget_plan(request.plan_id)
        if plan["project_id"] != project_id:
            raise ConflictError("Budget plan belongs to a different project")
        if not plan["is_current"]:
            raise ConflictError("A superseded budget plan cannot be approved")
        if plan["status"] == BudgetPlanStatus.WITHIN_TARGET.value:
            raise IntegrityError(
                "A plan within the automatic target does not need approval"
            )
        if plan["status"] == BudgetPlanStatus.APPROVED.value:
            existing = self.repository.budget_plan_approval(plan["id"])
            if existing and existing["approved_by"] == request.approved_by:
                return {"plan": plan, "approval": existing}
            raise ConflictError("Budget plan is already approved")
        approval = self.repository.record_budget_plan_approval(
            project_id,
            plan_id=plan["id"],
            plan_hash=plan["content_hash"],
            approved_total=Money.from_minor(
                plan["planned_total_minor"], plan["currency"]
            ),
            approved_by=request.approved_by,
            reason=request.reason,
        )
        return {
            "plan": self.repository.get_budget_plan(plan["id"]),
            "approval": approval,
        }

    def budget_plan(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.current_budget_plan(project_id)

    def budget_summary(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        plan = self.repository.current_budget_plan(project_id)
        available_minor = (
            project.max_budget.minor - project.spent.minor - project.reserved.minor
        )
        base = {
            "project_id": project_id,
            "currency": project.max_budget.currency,
            "project_budget": project.max_budget.model_dump(mode="json"),
            "project_reserved": project.reserved.model_dump(mode="json"),
            "project_spent": project.spent.model_dump(mode="json"),
            "available": Money.from_minor(
                available_minor, project.max_budget.currency
            ).model_dump(mode="json"),
            "automatic_run_cap": Money.from_minor(
                DEFAULT_RUN_CAP_MINOR.get(
                    project.max_budget.currency, project.max_budget.minor
                ),
                project.max_budget.currency,
            ).model_dump(mode="json"),
        }
        if not plan:
            return {
                **base,
                "plan": None,
                "pending_variances": self.repository.pending_variance_requests(
                    project_id
                ),
                "requires_approval": False,
                "requires_further_approval": False,
            }
        settled = [
            item
            for item in plan["items"]
            if item["status"] == BudgetItemStatus.SETTLED.value
        ]
        reserved = [
            item
            for item in plan["items"]
            if item["status"] == BudgetItemStatus.RESERVED.value
        ]
        settled_actual = sum(item["actual_minor"] or 0 for item in settled)
        settled_estimate = sum(item["estimated_minor"] for item in settled)
        variance = settled_actual - settled_estimate
        allowance = max(
            plan["planned_total_minor"] * plan["tolerance_bps"] // 10000,
            plan["tolerance_floor_minor"],
        )
        outstanding_required = [
            item
            for item in plan["items"]
            if item["priority"] == "required"
            and item["status"]
            in {BudgetItemStatus.PLANNED.value, BudgetItemStatus.RESERVED.value}
        ]
        return {
            **base,
            "plan": {
                "id": plan["id"],
                "version": plan["version"],
                "status": plan["status"],
                "content_hash": plan["content_hash"],
                "target": plan["target"],
                "planned_total": plan["planned_total"],
                "planned_total_minor": plan["planned_total_minor"],
                "tolerance_bps": plan["tolerance_bps"],
                "tolerance_allowance": Money.from_minor(
                    allowance, plan["currency"]
                ).model_dump(mode="json"),
                "tolerance_allowance_minor": allowance,
            },
            "items": {
                "total": len(plan["items"]),
                "planned": sum(
                    1
                    for item in plan["items"]
                    if item["status"] == BudgetItemStatus.PLANNED.value
                ),
                "reserved": len(reserved),
                "settled": len(settled),
                "outstanding_required": len(outstanding_required),
            },
            "execution": {
                "settled_estimated": Money.from_minor(
                    settled_estimate, plan["currency"]
                ).model_dump(mode="json"),
                "settled_actual": Money.from_minor(
                    settled_actual, plan["currency"]
                ).model_dump(mode="json"),
                "variance": Money.from_minor(
                    abs(variance), plan["currency"]
                ).model_dump(mode="json"),
                "variance_direction": (
                    "over" if variance > 0 else "under" if variance < 0 else "even"
                ),
                "variance_minor": variance,
                "within_tolerance": variance <= allowance,
                "remaining_in_plan": Money.from_minor(
                    max(0, plan["planned_total_minor"] - settled_actual),
                    plan["currency"],
                ).model_dump(mode="json"),
            },
            "pending_variances": self.repository.pending_variance_requests(project_id),
            "requires_approval": plan["status"]
            == BudgetPlanStatus.APPROVAL_REQUIRED.value,
            "requires_further_approval": bool(
                self.repository.pending_variance_requests(project_id)
            ),
            "plan_exceeded_tolerance": variance > allowance,
            "assets": self.asset_costs(project_id),
        }

    def _enforce_generation_routing(self, project_id: str, slot_id: str) -> None:
        """Paid generation is only allowed where the slot routing permits it."""

        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        approved = {source.lower() for source in slot["approved_sources"]}
        if not approved & GENERATED_SOURCES:
            raise RightsEligibilityError(
                "Paid generation is not an approved source for this slot; use owned, "
                "stock or deterministic composition, or record a new routing decision"
            )
        primary = self.repository.slot_asset_by_role(
            slot_id, SlotAssetRole.PRIMARY.value
        )
        if primary:
            artifact = self.repository.get_artifact(primary["artifact_id"])
            if artifact["status"] != ArtifactStatus.STALE.value:
                raise ConflictError(
                    "A current primary asset already satisfies this slot; replace it "
                    "before paying for generation"
                )

    def reserve_budget_item(
        self,
        project_id: str,
        plan_id: str,
        item_key: str,
        request: JobRequest,
    ) -> dict[str, Any]:
        """Reserve one planned line item before a provider call."""

        plan = self.repository.get_budget_plan(plan_id)
        if plan["project_id"] != project_id:
            raise ConflictError("Budget plan belongs to a different project")
        if not plan["is_current"]:
            raise ConflictError("A superseded budget plan cannot authorize work")
        if plan["status"] not in {
            BudgetPlanStatus.WITHIN_TARGET.value,
            BudgetPlanStatus.APPROVED.value,
        }:
            raise ApprovalRequiredError(
                "Budget plan requires human approval before execution"
            )
        item = next(
            (entry for entry in plan["items"] if entry["stable_key"] == item_key),
            None,
        )
        if not item:
            raise NotFoundError(f"Budget item not found in plan: {item_key}")
        if item["status"] != BudgetItemStatus.PLANNED.value:
            raise ConflictError(f"Budget item {item_key} is already {item['status']}")
        if (
            request.estimated_cost.minor != item["estimated_minor"]
            or request.estimated_cost.currency != plan["currency"]
        ):
            raise IntegrityError(
                "Job estimate must match the approved budget plan item"
            )
        if (
            request.provider != item["provider"]
            or request.operation != item["operation"]
        ):
            raise IntegrityError(
                "Job provider and operation must match the approved budget plan item"
            )
        if item["slot_id"]:
            self._enforce_generation_routing(project_id, item["slot_id"])
        job = self.reserve_job(project_id, request)
        linked = self.repository.link_budget_item_job(
            project_id, plan_id, item_key, job.id
        )
        return {"job": job.model_dump(mode="json"), "item": linked}

    @staticmethod
    def _blocked_studio_decisions(
        brief: dict[str, Any],
        request: StudioWorkPacketRequest,
        shots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        phases = {phase.value for phase in request.scope.phases}
        selected_shots = set(request.scope.shot_ids)
        selected_shots.update(
            shot["stable_key"] for shot in shots if shot["id"] in selected_shots
        )
        blocked: list[dict[str, Any]] = []
        for decision in brief["package"]["decisions"]:
            if decision["state"] != "blocked":
                continue
            affected = set(decision["blocked_work"])
            affects_phase = bool(affected & phases) or bool(affected & {"all", "*"})
            affects_prefixed_phase = any(
                item.split(":", 1)[0] in phases for item in affected
            )
            affects_shot = bool(affected & selected_shots)
            if affects_phase or affects_prefixed_phase or affects_shot:
                blocked.append(decision)
        return blocked

    def prepare_studio_work_packet(
        self, project_id: str, request: StudioWorkPacketRequest
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot authorize Studio work for a {project.status.value} project"
            )
        profile = self.repository.get_tool_profile(request.tool_profile_id)
        if profile["integration_mode"] not in {
            ToolIntegrationMode.MCP.value,
            ToolIntegrationMode.SPECIALIST_HANDOFF.value,
        }:
            raise IntegrityError(
                "Studio work requires an MCP or specialist-handoff tool profile"
            )
        operation_name = "openmontage.execute_work_packet"
        if operation_name not in profile["supported_operations"]:
            raise IntegrityError(
                f"Tool profile {profile['stable_key']} does not support {operation_name}"
            )
        readiness = self.studio_status()
        if not readiness["ready"]:
            raise ApprovalRequiredError(
                "Studio readiness must be verified before authorizing work: "
                f"{readiness['reason']}"
            )
        if request.required_approval_stage:
            approval = self.repository.active_approval(
                project_id, request.required_approval_stage.value
            )
            if not approval:
                raise ApprovalRequiredError(
                    f"Active {request.required_approval_stage.value} approval is required"
                )
        maximum_cost = self._resolve_run_cap(project, request.scope)
        auto_cap_minor = self._auto_run_cap_minor(maximum_cost.currency)
        approval_scope_hash = self._studio_spend_scope_hash(project_id, request)
        existing_for_request = self.repository.tool_operation_by_idempotency(
            project_id, request.tool_profile_id, request.idempotency_key
        )
        if maximum_cost.minor:
            available_minor = (
                project.max_budget.minor - project.spent.minor - project.reserved.minor
            )
            if not existing_for_request and maximum_cost.minor > available_minor:
                raise BudgetExceededError(
                    f"Insufficient budget: requested {maximum_cost.minor} minor units, "
                    f"available {available_minor}"
                )
            if maximum_cost.minor > auto_cap_minor:
                if not request.spend_approval_id:
                    raise ApprovalRequiredError(
                        "This Studio run exceeds the automatic per-run limit and "
                        "requires human spend approval"
                    )
                spend_approval = self.repository.get_studio_spend_approval(
                    request.spend_approval_id
                )
                if (
                    spend_approval["project_id"] != project_id
                    or spend_approval["scope_hash"] != approval_scope_hash
                    or spend_approval["max_cost_minor"] != maximum_cost.minor
                    or spend_approval["currency"] != maximum_cost.currency
                ):
                    raise ApprovalRequiredError(
                        "Studio spend approval does not match this exact work request"
                    )
                if spend_approval["consumed_operation_id"] and (
                    not existing_for_request
                    or existing_for_request["id"]
                    != spend_approval["consumed_operation_id"]
                ):
                    raise ConflictError(
                        "Studio spend approval has already been consumed"
                    )
            elif request.spend_approval_id:
                raise IntegrityError(
                    "Studio runs within the automatic limit do not use a spend approval"
                )
        elif request.spend_approval_id:
            raise IntegrityError("Zero-cost Studio runs do not use a spend approval")
        if existing_for_request:
            existing_scope_hash = existing_for_request["request"]["parameters"].get(
                "approval_scope_hash"
            )
            if existing_scope_hash != approval_scope_hash:
                raise ConflictError(
                    "Studio idempotency key was used for a different work request"
                )
            handoff = self.repository.specialist_handoff_for_operation(
                existing_for_request["id"]
            )
            packet_artifact = self.repository.get_artifact(
                handoff["work_packet_artifact_id"]
            )
            packet = json.loads(self.read_artifact_text(packet_artifact["id"]))
            budget_job = (
                self.repository.get_job(handoff["budget_job_id"])
                if handoff["budget_job_id"]
                else None
            )
            return {
                "handoff": handoff,
                "operation": existing_for_request,
                "work_packet_artifact": packet_artifact,
                "work_packet_hash": handoff["work_packet_hash"],
                "packet": packet,
                "budget_job": (
                    budget_job.model_dump(mode="json") if budget_job else None
                ),
            }
        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            raise IntegrityError("A current workable brief is required for Studio work")
        all_shots = self.repository.list_shots(project_id)
        shots_by_id = {shot["id"]: shot for shot in all_shots}
        if request.scope.shot_ids:
            missing_shots = set(request.scope.shot_ids) - set(shots_by_id)
            if missing_shots:
                raise NotFoundError(
                    f"Studio scope contains unknown shots: {sorted(missing_shots)}"
                )
            shots = [shots_by_id[shot_id] for shot_id in request.scope.shot_ids]
        else:
            shots = all_shots
        blockers = self._blocked_studio_decisions(brief, request, shots)
        if blockers:
            fields = ", ".join(decision["field"] for decision in blockers)
            raise ApprovalRequiredError(
                f"Studio scope is blocked by non-delegable decisions: {fields}"
            )
        phases = {phase.value for phase in request.scope.phases}
        scene_plan = self.repository.latest_scene_plan(project_id)
        if (
            phases & {"storyboard", "composition", "render", "review"}
            and not scene_plan
        ):
            raise IntegrityError(
                "Storyboard, composition, render and review work require a scene plan"
            )
        if "render" in phases and maximum_cost.minor:
            storyboard = self.repository.active_approval(
                project_id, ApprovalStage.ANIMATIC.value
            )
            if not storyboard:
                raise ApprovalRequiredError(
                    "Paid render work requires an approved storyboard/animatic first"
                )
        all_elements = self.repository.list_creative_elements(project_id)
        elements_by_id = {element["id"]: element for element in all_elements}
        if request.scope.element_ids:
            missing_elements = set(request.scope.element_ids) - set(elements_by_id)
            if missing_elements:
                raise NotFoundError(
                    "Studio scope contains unknown or superseded elements: "
                    f"{sorted(missing_elements)}"
                )
            elements = [
                elements_by_id[element_id] for element_id in request.scope.element_ids
            ]
        else:
            elements = all_elements
        selected_shot_ids = {shot["id"] for shot in shots}
        selected_element_ids = {element["id"] for element in elements}
        bindings = [
            binding
            for binding in self.repository.list_shot_element_bindings(project_id)
            if binding["shot_id"] in selected_shot_ids
            and binding["element_id"] in selected_element_ids
        ]
        references = []
        for reference in self.repository.list_shot_references(project_id):
            if reference["shot_id"] not in selected_shot_ids:
                continue
            artifact_id = reference["artifact_id"]
            if not artifact_id and reference["element_id"]:
                element = elements_by_id.get(reference["element_id"])
                artifact_id = element.get("source_artifact_id") if element else None
            if not artifact_id:
                references.append({**reference, "artifact": None})
                continue
            artifact = self.repository.get_artifact(artifact_id)
            references.append(
                {
                    **reference,
                    "artifact": {
                        "id": artifact["id"],
                        "relative_path": artifact["relative_path"],
                        "content_hash": artifact["content_hash"],
                    },
                }
            )
        reference_ids = [
            reference["artifact"]["id"]
            for reference in references
            if reference.get("artifact")
        ]
        slots = [
            slot
            for slot in self.repository.list_slots(project_id)
            if slot["shot_id"] in selected_shot_ids
        ]
        selected_slot_ids = {slot["id"] for slot in slots}
        slot_assets = [
            assignment
            for assignment in self.repository.list_slot_assets(project_id)
            if assignment["slot_id"] in selected_slot_ids
        ]
        if "render" in phases:
            gaps = []
            for slot in slots:
                roles = {
                    assignment["role"]
                    for assignment in slot_assets
                    if assignment["slot_id"] == slot["id"]
                }
                if not {"primary", "backup"} <= roles:
                    gaps.append(slot["stable_key"])
            if gaps:
                raise IntegrityError(
                    "Paid render requires a primary and backup asset for every slot: "
                    + ", ".join(sorted(gaps))
                )
        treatment_decisions = [
            decision
            for decision in self.repository.list_treatment_decisions(project_id)
            if decision["slot_id"] in selected_slot_ids
        ]
        audio_beds = self.repository.list_audio_beds(project_id)
        input_ids = [brief["source_artifact_id"], *request.input_artifact_ids]
        if scene_plan:
            input_ids.append(scene_plan["artifact_id"])
        input_ids.extend(
            element["source_artifact_id"]
            for element in elements
            if element["source_artifact_id"]
        )
        input_ids.extend(assignment["artifact_id"] for assignment in slot_assets)
        input_ids.extend(reference_ids)
        input_ids.extend(bed["artifact_id"] for bed in audio_beds)
        input_ids = list(dict.fromkeys(input_ids))
        input_artifacts = []
        for artifact_id in input_ids:
            artifact = self.repository.get_artifact(artifact_id)
            if artifact["project_id"] != project_id:
                raise IntegrityError(
                    f"Studio input artifact is not in the project: {artifact_id}"
                )
            input_artifacts.append(
                {
                    "id": artifact["id"],
                    "kind": artifact["kind"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                }
            )
        packet = {
            "schema": "pinaka.openmontage.work-packet/v1",
            "project": {
                "id": project.id,
                "name": project.name,
                "objective": project.objective,
                "audience": project.audience,
                "output_format": project.output_format,
                "language": project.language,
                "duration_seconds": str(project.duration_seconds),
                "budget": {
                    "maximum": project.max_budget.model_dump(mode="json"),
                    "reserved": project.reserved.model_dump(mode="json"),
                    "spent": project.spent.model_dump(mode="json"),
                    "this_run_cap": maximum_cost.model_dump(mode="json"),
                },
            },
            "brief": brief,
            "scene_plan": scene_plan,
            "shots": shots,
            "elements": elements,
            "shot_element_bindings": bindings,
            "references": references,
            "reference_summary": reference_summary(references),
            "generated_motion": {
                "requested": request.request_generated_motion,
                "reason": request.generated_motion_reason,
                "providers": (
                    list(SUPPORTED_PROVIDERS)
                    if request.request_generated_motion
                    else []
                ),
            },
            "provider_reference_hints": (
                {
                    provider: provider_reference_payload(references, provider)
                    for provider in SUPPORTED_PROVIDERS
                }
                if request.request_generated_motion
                else {}
            ),
            "slots": slots,
            "slot_asset_assignments": slot_assets,
            "treatment_decisions": treatment_decisions,
            "audio_beds": audio_beds,
            "scope": request.scope.model_dump(mode="json"),
            "instructions": request.instructions,
            "input_artifacts": input_artifacts,
            "output_declaration": request.output_declaration,
            "controller_authority": {
                "system_of_record": "pinaka.CreativeController",
                "studio_review_is_advisory": True,
                "exact_hash_approval_required": True,
                "publication_authorized": False,
                "paid_generation_authorized": bool(maximum_cost.minor),
                "generated_motion_authorized": request.request_generated_motion,
                "max_credits": 0,
                "max_amount": str(maximum_cost.amount),
                "max_currency": maximum_cost.currency,
                "automatic_spend_limit": Money.from_minor(
                    auto_cap_minor, maximum_cost.currency
                ).model_dump(mode="json"),
                "spend_approval_id": request.spend_approval_id,
            },
        }
        packet_bytes = canonical_json(packet).encode("utf-8")
        packet_hash = sha256_bytes(packet_bytes)
        dependencies = [
            DependencyRef(entity_type="workable_brief", entity_id=brief["id"]),
            *(
                [DependencyRef(entity_type="scene_plan", entity_id=scene_plan["id"])]
                if scene_plan
                else []
            ),
            *(
                DependencyRef(entity_type="shot", entity_id=shot["id"])
                for shot in shots
            ),
            *(
                DependencyRef(entity_type="creative_element", entity_id=element["id"])
                for element in elements
            ),
            *(
                DependencyRef(entity_type="artifact", entity_id=artifact_id)
                for artifact_id in input_ids
            ),
        ]
        packet_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.CONTRACT,
            relative_path=(
                f"artifacts/openmontage/work-packets/{packet_hash[:12]}.json"
            ),
            content=packet_bytes,
            operation={
                "type": "openmontage_work_packet",
                "schema": packet["schema"],
            },
            dependencies=tuple(dependencies),
        )
        operation_request = ToolOperationRequest(
            tool_profile_id=request.tool_profile_id,
            operation=operation_name,
            idempotency_key=request.idempotency_key,
            parameters={
                "work_packet_artifact_id": packet_artifact["id"],
                "work_packet_hash": packet_hash,
                "scope": request.scope.model_dump(mode="json"),
                "approval_scope_hash": approval_scope_hash,
                "spend_approval_id": request.spend_approval_id,
            },
            input_artifact_ids=(packet_artifact["id"], *input_ids),
            output_declaration=request.output_declaration,
            limits={
                "paid": bool(maximum_cost.minor),
                "max_cost_minor": maximum_cost.minor,
                "currency": maximum_cost.currency,
                "max_credits": 0,
                "allow_publication": False,
            },
            required_approval_stage=request.required_approval_stage,
        )
        budget_job = None
        if maximum_cost.minor:
            budget_request = JobRequest(
                provider="openmontage",
                endpoint="studio:production",
                operation=operation_name,
                idempotency_key=f"studio:{request.idempotency_key}",
                estimated_cost=maximum_cost,
                parameters={
                    "work_packet_artifact_id": packet_artifact["id"],
                    "work_packet_hash": packet_hash,
                    "approval_scope_hash": approval_scope_hash,
                    "spend_approval_id": request.spend_approval_id,
                },
                input_refs=(packet_artifact["id"], *input_ids),
            )
            operation_payload = operation_request.model_dump(mode="json")
            budget_payload = budget_request.model_dump(mode="json")
            operation, handoff, budget_job = self.repository.reserve_studio_work(
                project_id,
                tool_request=operation_request,
                tool_request_hash=sha256_json(operation_payload),
                budget_request=budget_request,
                budget_request_hash=sha256_json(budget_payload),
                work_packet_artifact_id=packet_artifact["id"],
                work_packet_hash=packet_hash,
                input_artifact_ids=tuple(input_ids),
                spend_approval_id=request.spend_approval_id,
                approval_scope_hash=approval_scope_hash,
                requires_spend_approval=(maximum_cost.minor > auto_cap_minor),
            )
        else:
            operation = self.reserve_tool_operation(project_id, operation_request)
            handoff = self.repository.record_specialist_handoff(
                project_id,
                tool_profile_id=request.tool_profile_id,
                operation_id=operation["id"],
                work_packet_artifact_id=packet_artifact["id"],
                work_packet_hash=packet_hash,
                input_artifact_ids=tuple(input_ids),
            )
        return {
            "handoff": handoff,
            "operation": operation,
            "work_packet_artifact": packet_artifact,
            "work_packet_hash": packet_hash,
            "packet": packet,
            "budget_job": (budget_job.model_dump(mode="json") if budget_job else None),
        }

    def studio_work_packet(self, operation_id: str) -> dict[str, Any]:
        handoff = self.repository.specialist_handoff_for_operation(operation_id)
        packet = json.loads(self.read_artifact_text(handoff["work_packet_artifact_id"]))
        return {"handoff": handoff, "packet": packet}

    def import_studio_result(
        self,
        request: StudioResultPacketImport,
        *,
        approved_variance_minor: int | None = None,
    ) -> dict[str, Any]:
        self._reject_sensitive_payload(request.model_dump(mode="json"), "studio_result")
        operation = self.repository.get_tool_operation(request.operation_id)
        if operation["operation"] != "openmontage.execute_work_packet":
            raise IntegrityError("Operation is not an OpenMontage work packet")
        handoff = self.repository.specialist_handoff_for_operation(request.operation_id)
        result_hash = sha256_json(request.model_dump(mode="json"))
        existing_receipt = self.repository.get_execution_receipt(request.operation_id)
        if existing_receipt:
            if handoff["result_packet_hash"] != result_hash:
                raise ConflictError(
                    "Studio operation already has a different terminal result"
                )
            return {
                "handoff": handoff,
                "receipt": existing_receipt,
                "artifacts": [
                    self.repository.get_artifact(artifact_id)
                    for artifact_id in existing_receipt["output_artifact_ids"]
                ],
            }
        declared_roles = set(operation["output_declaration"].get("roles", ()))
        artifacts = []
        for output in request.outputs:
            if declared_roles and output.role not in declared_roles:
                raise IntegrityError(
                    f"Studio output role was not declared: {output.role}"
                )
            if not output.relative_path.replace("\\", "/").startswith("artifacts/"):
                raise IntegrityError("Studio outputs must be stored beneath artifacts/")
            source = Path(output.source_path).expanduser().resolve()
            if not source.is_file():
                raise NotFoundError(f"Studio output file not found: {source}")
            content = source.read_bytes()
            if sha256_bytes(content) != output.content_hash:
                raise IntegrityError(
                    f"Studio output hash mismatch for role {output.role}"
                )
            artifacts.append(
                self.write_artifact(
                    operation["project_id"],
                    kind=output.kind,
                    relative_path=output.relative_path,
                    content=content,
                    operation={
                        "type": "openmontage_result",
                        "operation_id": request.operation_id,
                        "work_packet_hash": handoff["work_packet_hash"],
                        "result_packet_hash": result_hash,
                        "role": output.role,
                    },
                    parent_artifact_id=output.parent_artifact_id,
                    dependencies=(
                        DependencyRef(
                            entity_type="artifact",
                            entity_id=handoff["work_packet_artifact_id"],
                            entity_hash=handoff["work_packet_hash"],
                        ),
                        DependencyRef(
                            entity_type="tool_operation",
                            entity_id=request.operation_id,
                        ),
                    ),
                )
            )
        receipt_request = ExecutionReceiptCreate(
            status=request.status,
            elapsed_ms=request.elapsed_ms,
            output_artifact_ids=tuple(artifact["id"] for artifact in artifacts),
            response_hash=request.response_hash,
            actual_cost=request.actual_cost,
            details={
                **request.details,
                "integration": "openmontage",
                "work_packet_hash": handoff["work_packet_hash"],
                "result_packet_hash": result_hash,
            },
        )
        self._reject_sensitive_payload(
            receipt_request.model_dump(mode="json"), "studio_receipt"
        )
        try:
            receipt, handoff = self.repository.record_specialist_result(
                request.operation_id,
                receipt_request,
                result_packet_hash=result_hash,
                approved_variance_minor=approved_variance_minor,
                tool_version=(
                    str(request.details["tool_version"])
                    if request.details.get("tool_version")
                    else None
                ),
                operator_note=(
                    str(request.details["operator_note"])
                    if request.details.get("operator_note")
                    else None
                ),
            )
        except PinakaError:
            for artifact in artifacts:
                try:
                    discarded = self.repository.discard_artifact(artifact["id"])
                except (ConflictError, OSError):
                    discarded = False
                if discarded:
                    try:
                        self.artifact_store.resolve(
                            artifact["project_id"], artifact["relative_path"]
                        ).unlink(missing_ok=True)
                    except OSError:
                        pass
            raise
        return {"handoff": handoff, "receipt": receipt, "artifacts": artifacts}

    def run_studio_work_packet(
        self, operation_id: str, adapter: StudioAdapter
    ) -> dict[str, Any]:
        operation = self.repository.get_tool_operation(operation_id)
        handoff = self.repository.specialist_handoff_for_operation(operation_id)
        profile = self.repository.get_tool_profile(operation["tool_profile_id"])
        if profile["stable_key"] != adapter.profile_key:
            raise IntegrityError(
                f"Tool profile {profile['stable_key']!r} does not match adapter "
                f"{adapter.profile_key!r}"
            )
        existing = self.repository.get_execution_receipt(operation_id)
        if existing:
            return {
                "handoff": handoff,
                "receipt": existing,
                "artifacts": [
                    self.repository.get_artifact(artifact_id)
                    for artifact_id in existing["output_artifact_ids"]
                ],
            }
        operation = self.repository.mark_tool_operation_running(operation_id)
        packet = json.loads(self.read_artifact_text(handoff["work_packet_artifact_id"]))
        packet["packet_hash"] = handoff["work_packet_hash"]
        started = perf_counter()
        try:
            result = adapter.execute(packet)
        except TimeoutError:
            status = ToolOperationStatus.OUTCOME_UNKNOWN
            details = {"type": "TimeoutError", "redacted": True}
            result = None
        except (OSError, RuntimeError, ValueError) as error:
            status = ToolOperationStatus.FAILED
            details = {"type": type(error).__name__, "redacted": True}
            result = None
        else:
            status = ToolOperationStatus.SUCCEEDED
            details = result.details
        artifacts = []
        if result:
            for output in result.outputs:
                if not output.relative_path.replace("\\", "/").startswith("artifacts/"):
                    raise IntegrityError(
                        "Studio outputs must be stored beneath artifacts/"
                    )
                artifacts.append(
                    self.write_artifact(
                        operation["project_id"],
                        kind=output.kind,
                        relative_path=output.relative_path,
                        content=output.content,
                        operation={
                            "type": "openmontage_result",
                            "operation_id": operation_id,
                            "work_packet_hash": handoff["work_packet_hash"],
                            "role": output.role,
                            "simulated": True,
                        },
                        parent_artifact_id=output.parent_artifact_id,
                        dependencies=(
                            DependencyRef(
                                entity_type="artifact",
                                entity_id=handoff["work_packet_artifact_id"],
                                entity_hash=handoff["work_packet_hash"],
                            ),
                            DependencyRef(
                                entity_type="tool_operation", entity_id=operation_id
                            ),
                        ),
                    )
                )
        result_packet_hash = sha256_json(
            {
                "operation_id": operation_id,
                "status": status.value,
                "outputs": [
                    {
                        "id": artifact["id"],
                        "content_hash": artifact["content_hash"],
                    }
                    for artifact in artifacts
                ],
                "response_hash": result.response_hash if result else None,
                "details": details,
            }
        )
        receipt_request = ExecutionReceiptCreate(
            status=status,
            elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
            output_artifact_ids=tuple(artifact["id"] for artifact in artifacts),
            response_hash=result.response_hash if result else None,
            details={
                **details,
                "integration": "openmontage",
                "work_packet_hash": handoff["work_packet_hash"],
                "result_packet_hash": result_packet_hash,
            },
        )
        receipt, handoff = self.repository.record_specialist_result(
            operation_id,
            receipt_request,
            result_packet_hash=result_packet_hash,
            tool_version="deterministic-fake/v1",
        )
        return {"handoff": handoff, "receipt": receipt, "artifacts": artifacts}

    def _composition_layers(
        self, project_id: str, shots: list[dict[str, Any]]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        import base64

        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        shots_by_id = {shot["id"]: shot for shot in shots}
        layers: dict[str, list[dict[str, Any]]] = {}
        skipped: list[dict[str, Any]] = []
        total = 0
        for assignment in self.repository.list_slot_assets(project_id):
            slot = slots.get(assignment["slot_id"])
            if not slot:
                continue
            shot = shots_by_id.get(slot["shot_id"])
            if not shot:
                continue
            artifact = self.repository.get_artifact(assignment["artifact_id"])
            path = self.artifact_store.resolve(project_id, artifact["relative_path"])
            mime = _image_mime(path)
            if mime is None:
                skipped.append(
                    {
                        "artifact_id": artifact["id"],
                        "reason": f"unsupported media type {path.suffix!r}",
                    }
                )
                continue
            if not path.is_file():
                skipped.append(
                    {"artifact_id": artifact["id"], "reason": "file missing"}
                )
                continue
            data = path.read_bytes()
            if len(data) > MAX_LAYER_BYTES or total + len(data) > MAX_EMBEDDED_BYTES:
                skipped.append(
                    {"artifact_id": artifact["id"], "reason": "layer too large"}
                )
                continue
            total += len(data)
            layers.setdefault(shot["stable_key"], []).append(
                {
                    "role": slot["role"] or assignment["role"],
                    "mime": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            )
        return layers, skipped

    def author_composition(
        self,
        project_id: str,
        *,
        width: int = 1080,
        height: int = 1920,
        title: str | None = None,
        embed_layers: bool = True,
    ) -> dict[str, Any]:
        """Author one editable HyperFrames composition from the governed plan."""
        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot author a composition for a {project.status.value} project"
            )
        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            raise IntegrityError("A current workable brief is required")
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("A composition requires at least one planned shot")
        elements = self.repository.list_creative_elements(project_id)
        layers, skipped = (
            self._composition_layers(project_id, shots) if embed_layers else ({}, [])
        )
        html = build_composition(
            title=title or project.name,
            width=width,
            height=height,
            shots=shots,
            elements=elements,
            layers=layers,
        )
        report = validate_composition(html)
        if not report["valid"]:
            raise IntegrityError(
                "Composition failed validation: " + ", ".join(report["problems"])
            )
        content = html.encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"scenes/composition-{digest[:12]}/index.html",
            content=content,
            operation={
                "type": "hyperframes_composition",
                "schema": report.get("schema", "hyperframes"),
                "shots": report["shot_count"],
                "width": width,
                "height": height,
            },
            dependencies=(
                DependencyRef(entity_type="workable_brief", entity_id=brief["id"]),
                *(
                    DependencyRef(entity_type="shot", entity_id=shot["id"])
                    for shot in shots
                ),
                *(
                    DependencyRef(
                        entity_type="creative_element", entity_id=element["id"]
                    )
                    for element in elements
                ),
            ),
        )
        return {
            "artifact": artifact,
            "validation": report,
            "content_hash": digest,
            "shot_count": report["shot_count"],
            "layers_embedded": report["layer_count"],
            "layers_skipped": skipped,
        }

    def render_composition(
        self,
        project_id: str,
        *,
        composition_artifact_id: str | None = None,
        fps: int = 24,
        width: int = 1080,
        height: int = 1920,
        max_frames: int = 600,
        capture: FrameCaptureAdapter | None = None,
        encoder: VideoEncodeAdapter | None = None,
    ) -> dict[str, Any]:
        """Render one authored composition to a silent H.264 preview."""

        import tempfile
        from pathlib import Path as _Path

        if fps < 1 or fps > 60:
            raise IntegrityError("fps must be between 1 and 60")
        artifact = (
            self.repository.get_artifact(composition_artifact_id)
            if composition_artifact_id
            else self._latest_composition(project_id)
        )
        if not artifact:
            raise NotFoundError("No composition has been authored for this project")
        if artifact["project_id"] != project_id:
            raise ConflictError("Composition belongs to a different project")
        html = self.read_artifact_text(artifact["id"])
        report = validate_composition(html)
        if not report["valid"]:
            raise IntegrityError(
                "Composition failed validation: " + ", ".join(report["problems"])
            )
        duration = composition_duration(html)
        frame_count = int((duration * fps).to_integral_value(rounding="ROUND_CEILING"))
        if frame_count < 1:
            raise IntegrityError("Composition duration produces no frames")
        if frame_count > max_frames:
            raise IntegrityError(
                f"Render would produce {frame_count} frames; limit is {max_frames}"
            )
        html_path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        capture = capture or ChromeFrameCapture()
        encoder = encoder or FFmpegEncodeAdapter()
        captured = capture.capture(
            html_path=html_path,
            frame_count=frame_count,
            fps=fps,
            width=width,
            height=height,
        )
        with tempfile.TemporaryDirectory(prefix="pinaka-render-") as temp:
            frames_dir = _Path(temp)
            for frame in captured.frames:
                (frames_dir / f"frame-{frame.index:04d}.png").write_bytes(frame.content)
            encoded = encoder.encode(
                frames_dir=frames_dir,
                frame_count=frame_count,
                fps=fps,
                width=width,
                height=height,
            )
        duration_text = format(duration.normalize(), "f")
        digest = sha256_bytes(encoded.content)
        video = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/composition-{digest[:12]}.mp4",
            content=encoded.content,
            operation={
                "type": "composition_render",
                "composition_artifact_id": artifact["id"],
                "fps": fps,
                "frames": frame_count,
                "duration_seconds": duration_text,
                "width": width,
                "height": height,
                "encoder": encoder.profile_key,
                "command_hash": encoded.command_hash,
                "encoder_version": encoded.encoder_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                ),
            ),
        )
        return {
            "video": video,
            "composition": artifact,
            "fps": fps,
            "frames": frame_count,
            "duration_seconds": duration_text,
            "metadata": encoded.metadata,
            "command_hash": encoded.command_hash,
        }

    def synthesize_narration_vertex(
        self,
        project_id: str,
        *,
        script: str,
        voice: str = "Kore",
        model: str | None = None,
        adapter: NarrationAdapter | None = None,
        reserve: bool = False,
    ) -> dict[str, Any]:
        """Synthesize narration audio through Vertex TTS and register it."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot add narration to a {project.status.value} project"
            )
        text = (script or "").strip()
        if not text:
            raise IntegrityError("Narration script is empty")
        estimate_minor = estimate_tts_cost_minor(text)
        job = None
        if reserve:
            if project.max_budget.currency != "USD":
                raise IntegrityError(
                    "Narration reservation requires a USD project budget"
                )
            job = self.reserve_job(
                project_id,
                JobRequest(
                    provider="google-vertex",
                    endpoint="vertex:text-to-speech",
                    operation="speech.synthesize",
                    idempotency_key=(
                        "tts-" + sha256_json({"text": text, "voice": voice})[:20]
                    ),
                    estimated_cost=Money.from_minor(estimate_minor, "USD"),
                ),
            )
        try:
            narration = (
                adapter or VertexTTSAdapter(voice=voice, model=model or TTS_MODEL)
            ).synthesize(text)
        except Exception:
            if job is not None:
                self.settle_job(
                    job.id,
                    target=JobStatus.CANCELLED,
                    actual_cost=Money.from_minor(0, "USD"),
                )
            raise
        if job is not None:
            self.settle_job(
                job.id,
                target=JobStatus.SUCCEEDED,
                actual_cost=Money.from_minor(estimate_minor, "USD"),
            )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.AUDIO,
            relative_path=f"audio/narration-{narration.content_hash[:12]}.wav",
            content=narration.content,
            operation={
                "type": "tts",
                "provider": "google-vertex",
                "model": narration.model_name,
                "voice": narration.voice,
                "duration_seconds": narration.duration_seconds,
                "chars": len(text),
                "text_hash": narration.text_hash,
                "sample_rate": narration.sample_rate,
                "estimated_cost_minor": estimate_minor,
                "budget_job_id": job.id if job else None,
            },
        )
        return {
            "artifact": artifact,
            "duration_seconds": narration.duration_seconds,
            "voice": narration.voice,
            "model": narration.model_name,
            "text_hash": narration.text_hash,
            "reservation": job.model_dump(mode="json") if job else None,
        }

    def export_captions(self, project_id: str) -> dict[str, Any]:
        """Write a deterministic SRT from the shot plan."""

        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("Captions require a shot plan")
        srt = build_srt(shots)
        content = srt.encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"captions/captions-{digest[:12]}.srt",
            content=content,
            operation={
                "type": "captions",
                "cues": len(shots),
                "source": "shot_plan",
            },
            dependencies=tuple(
                DependencyRef(entity_type="shot", entity_id=shot["id"])
                for shot in shots
            ),
        )
        return {"artifact": artifact, "cues": len(shots), "srt": srt}

    def burn_captions(
        self,
        project_id: str,
        *,
        video_artifact_id: str,
        captions_artifact_id: str,
        adapter: Any | None = None,
        mode: str = "soft",
    ) -> dict[str, Any]:
        """Attach the SRT as a soft subtitle track on the rendered video."""

        import tempfile
        from pathlib import Path as _Path

        video = self.repository.get_artifact(video_artifact_id)
        subtitles = self.repository.get_artifact(captions_artifact_id)
        for artifact in (video, subtitles):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        if not subtitles["relative_path"].endswith(".srt"):
            raise IntegrityError("Caption artifact must be an SRT")
        if mode not in {"soft", "hard"}:
            raise IntegrityError("Caption mode must be 'soft' or 'hard'")
        video_path = self.artifact_store.resolve(project_id, video["relative_path"])
        srt_path = self.artifact_store.resolve(project_id, subtitles["relative_path"])
        with tempfile.TemporaryDirectory(prefix="pinaka-captions-") as temp:
            output = _Path(temp) / "captioned.mp4"
            if mode == "hard":
                result = (adapter or BurnedCaptionAdapter()).burn(
                    video_path=video_path, srt_path=srt_path, output_path=output
                )
            else:
                result = (adapter or CaptionMuxAdapter()).mux(
                    video_path=video_path, srt_path=srt_path, output_path=output
                )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/captioned-{result.content_hash[:12]}.mp4",
            content=result.content,
            operation={
                "type": "caption_burn",
                "mode": mode,
                "video_artifact_id": video["id"],
                "captions_artifact_id": subtitles["id"],
                "subtitle_hash": result.subtitle_hash,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video["id"],
                    entity_hash=video["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=subtitles["id"],
                    entity_hash=subtitles["content_hash"],
                ),
            ),
        )
        return {"video": artifact, "metadata": result.metadata}

    def mux_narration(
        self,
        project_id: str,
        *,
        video_artifact_id: str,
        audio_artifact_id: str,
        adapter: MuxAdapter | None = None,
    ) -> dict[str, Any]:
        """Mux normalized narration over the silent composition render."""

        video = self.repository.get_artifact(video_artifact_id)
        audio = self.repository.get_artifact(audio_artifact_id)
        for artifact in (video, audio):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        if audio["kind"] != ArtifactKind.AUDIO.value:
            raise IntegrityError("Narration artifact must be audio")
        duration = float(
            video["operation"].get("duration_seconds")
            or audio["operation"].get("duration_seconds")
            or 0
        )
        if duration <= 0:
            raise IntegrityError("Cannot mux without a known duration")
        video_path = self.artifact_store.resolve(project_id, video["relative_path"])
        audio_path = self.artifact_store.resolve(project_id, audio["relative_path"])
        for path in (video_path, audio_path):
            if not path.is_file():
                raise NotFoundError(f"Media file missing: {path.name}")
        muxed = (adapter or FFmpegMuxAdapter()).mux(
            video_path=video_path,
            audio_path=audio_path,
            duration_seconds=duration,
        )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/narrated-{muxed.content_hash[:12]}.mp4",
            content=muxed.content,
            operation={
                "type": "narration_mux",
                "duration_seconds": duration,
                "video_artifact_id": video["id"],
                "audio_artifact_id": audio["id"],
                "loudness": muxed.loudness,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video["id"],
                    entity_hash=video["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=audio["id"],
                    entity_hash=audio["content_hash"],
                ),
            ),
        )
        return {
            "video": artifact,
            "loudness": muxed.loudness,
            "metadata": muxed.metadata,
        }

    def _latest_composition(self, project_id: str) -> dict[str, Any] | None:
        for artifact in reversed(self.repository.list_artifacts(project_id)):
            if artifact["kind"] == ArtifactKind.DESIGN.value and artifact[
                "relative_path"
            ].startswith("scenes/composition-"):
                return artifact
        return None

    def authorize_publication(
        self, project_id: str, request: PublicationAuthorizationCreate
    ) -> dict[str, Any]:
        """Record a human publication authorization for the approved final hash."""

        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.FINAL_APPROVED,
            ProjectStatus.ARCHIVED,
        }:
            raise ConflictError(
                "Publication authorization requires a final-approved or archived project"
            )
        final = self.repository.active_approval(project_id, ApprovalStage.FINAL.value)
        if not final or not final.get("output_hash"):
            raise ApprovalRequiredError(
                "An active final approval with an exact output hash is required"
            )
        artifact = self.repository.artifact_by_content_hash(
            project_id, final["output_hash"]
        )
        if not artifact:
            raise NotFoundError(
                "The approved final artifact is no longer current; re-approve it first"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "publication_authorization"
        )
        return self.repository.record_publication_authorization(
            project_id,
            artifact_id=artifact["id"],
            artifact_hash=artifact["content_hash"],
            channels=request.channels,
            authorized_by=request.authorized_by,
            reference=request.reference,
            expires_at=request.expires_at,
        )

    def publication_authorization(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.current_publication_authorization(project_id)

    def stored_publication_authorization(
        self, project_id: str, channel: str
    ) -> PublicationAuthorization:
        """Build the publication authorization for one channel from the record."""

        record = self.repository.current_publication_authorization(project_id)
        if not record or channel not in record["channels"]:
            return PublicationAuthorization(authorized=False)
        if record["expires_at"]:
            try:
                expires = datetime.fromisoformat(
                    record["expires_at"].replace("Z", "+00:00")
                )
            except ValueError:
                return PublicationAuthorization(authorized=False)
            if expires <= datetime.now(timezone.utc):
                return PublicationAuthorization(authorized=False)
        return PublicationAuthorization(
            authorized=True,
            authorized_by=record["authorized_by"],
            authorized_at=datetime.fromisoformat(
                record["authorized_at"].replace("Z", "+00:00")
            ),
            artifact_hash=record["artifact_hash"],
            channels=tuple(record["channels"]),
            reference=record["reference"],
            expires_at=(
                datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
                if record["expires_at"]
                else None
            ),
        )

    def qa_render(
        self,
        project_id: str,
        *,
        artifact_id: str,
        expected_duration: float | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
        expected_fps: float | None = None,
        expect_audio: bool = True,
        captions_artifact_id: str | None = None,
        probe: Any | None = None,
        measure_loudness: Any | None = None,
    ) -> dict[str, Any]:
        """Technical QA on a rendered deliverable, recorded per check."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        if not self.verify_artifact(artifact_id):
            raise IntegrityError("Rendered file does not match its registered hash")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        metadata = (probe or FFmpegEncodeAdapter().probe)(path)
        checks: list[dict[str, Any]] = []

        def record(
            name: str, status: str, detail: str, evidence: dict[str, Any]
        ) -> None:
            self.repository.record_qa_result(
                project_id=project_id,
                artifact_id=artifact_id,
                check_name=name,
                status=status,
                findings=evidence,
                evidence_hash=artifact["content_hash"],
            )
            checks.append(
                {
                    "check": name,
                    "status": status,
                    "detail": detail,
                    "evidence": evidence,
                }
            )

        container = str(metadata.get("format", "")).lower()
        container_tokens = {token.strip() for token in container.split(",")}
        record(
            "technical.container",
            "pass" if container_tokens & {"mp4", "mov", "m4a"} else "fail",
            f"container {container or 'unknown'}",
            {"format": metadata.get("format")},
        )
        codec = str(metadata.get("codec", "")).lower()
        record(
            "technical.codec",
            "pass" if codec in {"h264", "hevc"} else "warn",
            f"video codec {codec or 'unknown'}",
            {"codec": metadata.get("codec")},
        )
        duration = float(metadata.get("duration_seconds") or 0)
        if expected_duration is None:
            record(
                "technical.duration",
                "pass",
                f"duration {duration}s",
                {"duration_seconds": duration},
            )
        else:
            delta = abs(duration - expected_duration)
            status = "pass" if delta <= 0.25 else "warn" if delta <= 1.0 else "fail"
            record(
                "technical.duration",
                status,
                f"duration {duration}s vs expected {expected_duration}s",
                {"duration_seconds": duration, "expected": expected_duration},
            )
        width = int(metadata.get("width_px") or 0)
        height = int(metadata.get("height_px") or 0)
        resolution_ok = True
        if expected_width is not None:
            resolution_ok = resolution_ok and width == expected_width
        if expected_height is not None:
            resolution_ok = resolution_ok and height == expected_height
        record(
            "technical.resolution",
            "pass" if resolution_ok else "fail",
            f"{width}x{height}",
            {
                "width_px": width,
                "height_px": height,
                "expected_width": expected_width,
                "expected_height": expected_height,
            },
        )
        if expected_fps is not None:
            fps = float(metadata.get("fps") or 0)
            record(
                "technical.frame_rate",
                "pass" if abs(fps - expected_fps) <= 0.51 else "warn",
                f"{fps} fps",
                {"fps": fps, "expected": expected_fps},
            )
        has_audio = bool(metadata.get("has_audio"))
        record(
            "technical.audio",
            "pass" if has_audio == expect_audio else "fail",
            f"audio stream {'present' if has_audio else 'absent'}",
            {"has_audio": has_audio, "expected": expect_audio},
        )
        if has_audio and expect_audio:
            try:
                loudness = (measure_loudness or FFmpegMuxAdapter().loudness)(path)
            except (OSError, RuntimeError, ValueError):
                record(
                    "audio.loudness",
                    "warn",
                    "loudness could not be measured",
                    {},
                )
            else:
                integrated = _as_float(loudness.get("input_i"))
                peak = _as_float(loudness.get("input_tp"))
                if integrated is None:
                    loudness_status = "warn"
                elif -24.0 <= integrated <= -9.0:
                    loudness_status = "pass"
                elif -27.0 <= integrated <= -6.0:
                    loudness_status = "warn"
                else:
                    loudness_status = "fail"
                record(
                    "audio.loudness",
                    loudness_status,
                    f"integrated {integrated if integrated is not None else 'n/a'} LUFS",
                    {"integrated_lufs": integrated},
                )
                if peak is not None:
                    peak_status = (
                        "pass" if peak <= -1.0 else "warn" if peak <= 0.0 else "fail"
                    )
                    record(
                        "audio.true_peak",
                        peak_status,
                        f"true peak {peak:.1f} dBTP",
                        {"true_peak_dbtp": peak},
                    )
        if captions_artifact_id:
            captions = self.repository.get_artifact(captions_artifact_id)
            if captions["project_id"] != project_id:
                raise ConflictError("Caption artifact belongs to a different project")
            srt_path = self.artifact_store.resolve(
                project_id, captions["relative_path"]
            )
            covered, cues, overrun = _caption_coverage(
                srt_path.read_text(encoding="utf-8"), duration
            )
            ratio = covered / duration if duration > 0 else 0.0
            coverage_status = (
                "pass" if ratio >= 0.9 else "warn" if ratio >= 0.7 else "fail"
            )
            record(
                "captions.coverage",
                coverage_status,
                f"{cues} cues covering {ratio * 100:.0f}% of the runtime",
                {
                    "cues": cues,
                    "covered_seconds": round(covered, 3),
                    "ratio": round(ratio, 3),
                },
            )
            record(
                "captions.timing",
                "fail" if overrun else "pass",
                "cue extends past the runtime" if overrun else "all cues fit",
                {"overrun": overrun},
            )

        statuses = {check["status"] for check in checks}
        overall = (
            QAStatus.FAIL.value
            if QAStatus.FAIL.value in statuses
            else QAStatus.WARN.value
            if QAStatus.WARN.value in statuses
            else QAStatus.PASS.value
        )
        report = {
            "schema": "pinaka.render-qa/v1",
            "project_id": project_id,
            "artifact_id": artifact_id,
            "content_hash": artifact["content_hash"],
            "overall": overall,
            "metadata": metadata,
            "checks": checks,
        }
        content = canonical_json(report).encode("utf-8")
        digest = sha256_bytes(content)
        report_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"qa/render-report-{digest[:12]}.json",
            content=content,
            operation={
                "type": "render_qa",
                "artifact_id": artifact_id,
                "overall": overall,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact_id,
                    entity_hash=artifact["content_hash"],
                ),
            ),
        )
        return {
            "overall": overall,
            "checks": checks,
            "report": report,
            "report_artifact": report_artifact,
        }

    def studio_production(self, operation_id: str) -> dict[str, Any] | None:
        production = self.repository.get_studio_production(operation_id)
        if not production:
            return None
        return {
            "production": production,
            "jobs": self.repository.studio_jobs(production["id"]),
        }

    def run_studio_production(
        self,
        project_id: str,
        operation_id: str,
        transport: StudioTransport,
        *,
        max_polls: int = 3,
        requested_by: str | None = None,
    ) -> dict[str, Any]:
        """Drive one authorized Studio production to completion or a resumable step."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot run Studio work for a {project.status.value} project"
            )
        readiness = self.studio_status()
        if not readiness["ready"]:
            raise ApprovalRequiredError(
                "Studio readiness must be verified before a live run: "
                f"{readiness['reason']}"
            )
        operation = self.repository.get_tool_operation(operation_id)
        if operation["project_id"] != project_id:
            raise ConflictError("Operation belongs to a different project")
        if operation["operation"] != "openmontage.execute_work_packet":
            raise IntegrityError("Operation is not an OpenMontage work packet")
        handoff = self.repository.specialist_handoff_for_operation(operation_id)
        if not handoff["budget_job_id"]:
            raise IntegrityError("A Studio production requires a reserved budget job")
        budget_job = self.repository.get_job(handoff["budget_job_id"])
        packet = json.loads(self.read_artifact_text(handoff["work_packet_artifact_id"]))
        packet["packet_hash"] = handoff["work_packet_hash"]

        existing_receipt = self.repository.get_execution_receipt(operation_id)
        if existing_receipt:
            return {
                "status": "settled",
                "receipt": existing_receipt,
                "production": self.repository.get_studio_production(operation_id),
            }

        production = self.repository.get_studio_production(operation_id)
        if production is None:
            if budget_job.status != JobStatus.BUDGET_RESERVED:
                raise ConflictError(
                    f"Budget job is {budget_job.status.value}; a run needs a fresh reservation"
                )
            open_request = StudioOpenRequest(
                project_id=project_id,
                title=f"{project.name} — {handoff['work_packet_hash'][:8]}",
                intent=project.objective,
                autonomy=str(packet["scope"].get("autonomy", "autopilot")),
                max_cost_minor=budget_job.reserved_cost.minor,
                currency=budget_job.reserved_cost.currency,
                packet_hash=handoff["work_packet_hash"],
                constraints={
                    "publication": False,
                    "paid_generation": True,
                    "exact_hash_approval_required": True,
                    "system_of_record": "pinaka.CreativeController",
                },
            )
            try:
                opened = transport.open_production(open_request)
            except StudioPending as pending:
                return {
                    "status": "pending",
                    "step": pending.step,
                    "message": str(pending),
                    "production": None,
                }
            production = self.repository.open_studio_production(
                project_id,
                operation_id=operation_id,
                transport=transport.name,
                title=open_request.title,
                autonomy=open_request.autonomy,
                budget_minor=open_request.max_cost_minor,
                currency=open_request.currency,
                packet_hash=handoff["work_packet_hash"],
                external_production_id=opened.external_production_id,
                status="opened",
            )

        jobs = self.repository.studio_jobs(production["id"])
        if not jobs:
            try:
                submission = transport.submit(production, packet)
            except StudioPending as pending:
                return {
                    "status": "pending",
                    "step": pending.step,
                    "message": str(pending),
                    "production": production,
                }
            job_row = self.repository.record_studio_job(
                production["id"],
                external_job_id=submission.external_job_id,
                phase="work_packet",
                status=submission.status,
                request_hash=handoff["work_packet_hash"],
                detail=submission.raw,
            )
            production = self.repository.update_studio_production(
                production["id"], status="submitted"
            )
        else:
            job_row = jobs[0]
            submission = StudioSubmission(
                external_job_id=job_row["external_job_id"],
                status=job_row["status"],
                raw=job_row["detail"] or {},
            )

        terminal = {"completed", "failed", "cancelled"}
        for _ in range(max(1, max_polls)):
            if job_row["status"] in terminal:
                break
            try:
                polled = transport.poll(production, submission)
            except StudioPending as pending:
                return {
                    "status": "pending",
                    "step": pending.step,
                    "message": str(pending),
                    "production": production,
                }
            job_row = self.repository.update_studio_job(
                job_row["id"], status=polled.status, detail=polled.detail
            )
            if polled.status in terminal:
                break

        if job_row["status"] not in terminal:
            production = self.repository.update_studio_production(
                production["id"], status="working"
            )
            return {
                "status": "pending",
                "step": "poll",
                "message": "Studio is still working; re-run to poll again",
                "production": production,
            }

        if job_row["status"] != "completed":
            outcome = ToolOperationStatus.FAILED
            if job_row["status"] == "cancelled":
                outcome = ToolOperationStatus.CANCELLED
            result = self.import_studio_result(
                StudioResultPacketImport(
                    operation_id=operation_id,
                    status=outcome,
                    elapsed_ms=0,
                    actual_cost=Money(
                        amount=Decimal(0),
                        currency=budget_job.reserved_cost.currency,
                    ),
                    details={
                        "integration": "openmontage",
                        "transport": transport.name,
                        "external_job_id": submission.external_job_id,
                        "studio_status": job_row["status"],
                    },
                )
            )
            production = self.repository.update_studio_production(
                production["id"],
                status=job_row["status"],
                last_error=f"Studio job {job_row['status']}",
            )
            return {"status": job_row["status"], "production": production, **result}

        try:
            collected = transport.collect(production, submission)
        except StudioPending as pending:
            return {
                "status": "pending",
                "step": pending.step,
                "message": str(pending),
                "production": production,
            }

        approved = self.repository.approved_variance_for_job(
            budget_job.id, collected.actual_cost_minor
        )
        result_packet = StudioResultPacketImport(
            operation_id=operation_id,
            status=ToolOperationStatus.SUCCEEDED,
            elapsed_ms=0,
            outputs=tuple(
                StudioResultOutputImport(
                    source_path=output.source_path,
                    relative_path=output.relative_path,
                    kind=output.kind,
                    content_hash=output.content_hash,
                    role=output.role,
                    parent_artifact_id=output.parent_artifact_id,
                )
                for output in collected.outputs
            ),
            actual_cost=Money.from_minor(
                collected.actual_cost_minor, budget_job.reserved_cost.currency
            ),
            details={
                "integration": "openmontage",
                "transport": transport.name,
                "external_production_id": production["external_production_id"],
                "external_job_id": submission.external_job_id,
            },
        )
        try:
            imported = self.import_studio_result(
                result_packet,
                approved_variance_minor=(
                    collected.actual_cost_minor if approved else None
                ),
            )
        except BudgetExceededError:
            request = self.repository.record_variance_request(
                project_id,
                kind="studio_run",
                plan_id=None,
                item_id=None,
                operation_id=operation_id,
                job_id=budget_job.id,
                estimated_minor=budget_job.reserved_cost.minor,
                actual_minor=collected.actual_cost_minor,
                allowed_minor=budget_job.reserved_cost.minor,
                currency=budget_job.reserved_cost.currency,
                requested_by=requested_by,
                reason="Studio run cost exceeded its controller reservation",
            )
            production = self.repository.update_studio_production(
                production["id"], status="awaiting_approval"
            )
            return {
                "status": "approval_required",
                "message": (
                    "Studio reported a cost above the run reservation. Approve or "
                    f"reject request {request['id']}, then run again."
                ),
                "request": request,
                "production": production,
            }
        production = self.repository.update_studio_production(
            production["id"], status="completed"
        )
        return {"status": "completed", "production": production, **imported}

    def register_vertex_profile(
        self,
        *,
        project: str | None = None,
        location: str | None = None,
        enabled: bool = False,
    ) -> dict[str, Any]:
        """Register the ADC-authenticated Vertex provider profile (disabled)."""

        return self.register_provider_profile(
            vertex_provider_profile(project=project, location=location, enabled=enabled)
        )

    def verify_vertex_models(
        self,
        *,
        client: Any | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """Free availability check for the Vertex models Pinaka depends on."""

        results = verify_models(client=client, project=project, location=location)
        for result in results:
            self.repository.record_capability(result)
        ready = all(result.status == CapabilityStatus.WORKING for result in results)
        return {
            "ready": ready,
            "project": project or DEFAULT_PROJECT,
            "location": location or DEFAULT_LOCATION,
            "auth": "application_default_credentials",
            "models": [result.model_dump(mode="json") for result in results],
        }

    def enhance_still_with_vertex(
        self,
        project_id: str,
        *,
        tool_profile_id: str,
        source_artifact_id: str,
        prompt: str,
        model: str = "gemini-3.1-flash-image",
        image_size: str = "1K",
        aspect_ratio: str = "16:9",
        adapter: Any | None = None,
        plan_id: str | None = None,
        item_key: str | None = None,
    ) -> dict[str, Any]:
        """Enhance a sourced still through the Vertex image adapter."""

        source = self.repository.get_artifact(source_artifact_id)
        if source["project_id"] != project_id:
            raise ConflictError("Source artifact belongs to a different project")
        if source["kind"] == ArtifactKind.ORIGINAL or source["immutable"]:
            raise IntegrityError("Enhance a working copy, not a protected original")
        if not self.verify_artifact(source_artifact_id):
            raise IntegrityError("Source artifact file does not match its hash")
        path = self.artifact_store.resolve(project_id, source["relative_path"])
        ceiling = estimate_cost_minor(model, image_size)
        request = ToolOperationRequest(
            tool_profile_id=tool_profile_id,
            operation="image.edit",
            idempotency_key=(
                f"vertex-edit-{source['content_hash'][:12]}-"
                f"{sha256_json({'prompt': prompt, 'model': model})[:12]}"
            ),
            parameters={
                "prompt": prompt,
                "model": model,
                "image_size": image_size,
                "aspect_ratio": aspect_ratio,
                "input_image_path": str(path),
            },
            limits={"max_cost_minor": ceiling, "paid": True},
            output_declaration={"media_type": "image", "model": model},
            required_approval_stage=ApprovalStage.SOURCING,
        )
        job_id = None
        if plan_id and item_key:
            plan = self.repository.get_budget_plan(plan_id)
            item = next(
                (entry for entry in plan["items"] if entry["stable_key"] == item_key),
                None,
            )
            if not item:
                raise NotFoundError(f"Budget item not found in plan: {item_key}")
            reserved = self.reserve_budget_item(
                project_id,
                plan_id,
                item_key,
                JobRequest(
                    provider="google-vertex",
                    endpoint="vertex:image-edit",
                    operation="image.edit",
                    idempotency_key=request.idempotency_key,
                    estimated_cost=Money.from_minor(
                        item["estimated_minor"], plan["currency"]
                    ),
                    parameters={"prompt": prompt, "model": model},
                ),
            )
            job_id = reserved["job"]["id"]
        try:
            operation, receipt, artifact = self.run_tool_once(
                project_id,
                request,
                adapter or GoogleVertexImageAdapter(model=model),
                artifact_path=(
                    f"artifacts/vertex-edit-{source['content_hash'][:12]}.png"
                ),
                artifact_kind=ArtifactKind.DERIVATIVE,
                parent_artifact_id=source_artifact_id,
                dependencies=(
                    DependencyRef(
                        entity_type="artifact",
                        entity_id=source_artifact_id,
                        entity_hash=source["content_hash"],
                    ),
                ),
            )
        except Exception:
            if job_id:
                self.settle_job(
                    job_id,
                    target=JobStatus.CANCELLED,
                    actual_cost=Money.from_minor(
                        0, self.repository.get_job(job_id).estimated_cost.currency
                    ),
                )
            raise
        settlement = None
        if job_id:
            actual_minor = int(
                (receipt or {}).get("details", {}).get("actual_cost_minor", ceiling)
            )
            settlement = self.settle_budget_item(
                project_id,
                job_id,
                actual_cost=Money.from_minor(
                    actual_minor,
                    self.repository.get_job(job_id).estimated_cost.currency,
                ),
                requested_by="controller:vertex-image",
                reason="Vertex image edit settled after execution",
                asset_artifact_id=artifact["id"] if artifact else None,
            )
        return {
            "operation": operation,
            "receipt": receipt,
            "artifact": artifact,
            "settlement": settlement,
        }

    def run_tool_once(
        self,
        project_id: str,
        request: ToolOperationRequest,
        adapter: ToolAdapter,
        *,
        artifact_path: str,
        artifact_kind: ArtifactKind = ArtifactKind.DERIVATIVE,
        dependencies: tuple[DependencyRef, ...] = (),
        parent_artifact_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        profile = self.repository.get_tool_profile(request.tool_profile_id)
        if profile["stable_key"] != adapter.profile_key:
            raise IntegrityError(
                f"Tool profile {profile['stable_key']!r} does not match adapter "
                f"{adapter.profile_key!r}"
            )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            artifact = None
            output_ids = existing_receipt["output_artifact_ids"]
            if output_ids:
                artifact = self.repository.get_artifact(output_ids[0])
            return operation, existing_receipt, artifact
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError(
                "Tool operation outcome is unknown and requires reconciliation"
            )
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        try:
            result = adapter.execute(request)
        except TimeoutError:
            receipt = self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.OUTCOME_UNKNOWN,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": "TimeoutError", "redacted": True},
                ),
            )
            return self.repository.get_tool_operation(operation["id"]), receipt, None
        except (OSError, RuntimeError, ValueError) as error:
            receipt = self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            return self.repository.get_tool_operation(operation["id"]), receipt, None
        artifact = self.write_artifact(
            project_id,
            kind=artifact_kind,
            relative_path=artifact_path,
            content=result.output_bytes,
            operation={
                "type": "tool_operation",
                "operation_id": operation["id"],
                "tool_profile": profile["stable_key"],
            },
            parent_artifact_id=parent_artifact_id,
            dependencies=(
                DependencyRef(entity_type="tool_operation", entity_id=operation["id"]),
                *dependencies,
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(artifact["id"],),
                response_hash=result.response_hash,
                details=result.details,
            ),
        )
        return self.repository.get_tool_operation(operation["id"]), receipt, artifact

    def _animatic_operation(
        self,
        tool_profile_id: str,
        operation: str,
        deliverable: dict[str, Any],
        timeline: dict[str, Any],
        suffix: str,
        output_declaration: dict[str, Any],
    ) -> ToolOperationRequest:
        return ToolOperationRequest(
            tool_profile_id=tool_profile_id,
            operation=operation,
            idempotency_key=(
                f"animatic-{suffix}-{deliverable['stable_key']}-"
                f"{timeline['timeline_hash'][:12]}"
            ),
            parameters={"timeline": timeline},
            output_declaration=output_declaration,
            limits={"paid": False, "max_cost_minor": 0},
            required_approval_stage=ApprovalStage.SOURCING,
        )

    def _pilot_timeline_context(
        self, project_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        deliverables = self.repository.list_deliverables(project_id)
        if len(deliverables) != 1:
            raise IntegrityError(
                "Pilot animatic production requires exactly one deliverable"
            )
        deliverable = deliverables[0].model_dump(mode="json")
        segments = [
            segment
            for segment in self.repository.list_segments(project_id)
            if segment["deliverable_id"] == deliverable["id"]
        ]
        if not segments:
            raise IntegrityError("Pilot animatic production requires planned segments")

        bundles = {
            bundle["kind"]: bundle
            for bundle in self.repository.current_grounding_bundles(project_id)
        }
        missing = {"brand", "claims", "sources"} - set(bundles)
        if missing:
            raise IntegrityError(
                "Animatic production requires current grounding: "
                + ", ".join(sorted(missing))
            )

        try:
            timeline = build_timeline(
                project_id=project_id,
                deliverable=deliverable,
                segments=segments,
                brand_values=bundles["brand"]["values"],
                claims_values=bundles["claims"]["values"],
            )
        except ValueError as error:
            raise IntegrityError(f"Animatic timeline is invalid: {error}") from error
        return deliverable, segments, bundles, timeline

    def _latest_animatic_bundle(
        self, project_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("previews/animatic-manifest-")
        ]
        if not manifests:
            return None
        manifest_artifact = max(manifests, key=lambda item: item["created_at"])
        manifest = json.loads(self.read_artifact_text(manifest_artifact["id"]))
        artifacts = {
            name: self.repository.get_artifact(reference["id"])
            for name, reference in manifest["artifacts"].items()
        }
        return manifest_artifact, manifest, artifacts

    def produce_animatic(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.SOURCING,
            ProjectStatus.ANIMATIC_READY,
        }:
            raise ApprovalRequiredError(
                "Animatic production requires an approved sourcing state"
            )
        if (
            project.status == ProjectStatus.SOURCING
            and not self.repository.active_approval(
                project_id, ApprovalStage.SOURCING.value
            )
        ):
            raise ApprovalRequiredError(
                "An active sourcing approval is required before animatic production"
            )

        deliverable, segments, bundles, timeline = self._pilot_timeline_context(
            project_id
        )
        key = timeline["timeline_hash"][:12]
        manifest_path = f"previews/animatic-manifest-{key}.json"
        existing_manifest_artifact = self.repository.artifact_by_path(
            project_id, manifest_path
        )
        if existing_manifest_artifact:
            manifest = json.loads(
                self.read_artifact_text(existing_manifest_artifact["id"])
            )
            artifacts = {
                name: self.repository.get_artifact(reference["id"])
                for name, reference in manifest["artifacts"].items()
            }
            if project.status == ProjectStatus.SOURCING:
                project = self.transition_project(
                    project_id,
                    ProjectStatus.ANIMATIC_READY,
                    reason="Deterministic animatic bundle already rendered",
                )
            return {
                "project": project.model_dump(mode="json"),
                "timeline": timeline,
                "artifacts": artifacts,
                "manifest_artifact": existing_manifest_artifact,
                "resumed": True,
                "next_action": self.next_action(project_id),
            }

        paths = {
            "timeline": f"artifacts/timeline-{key}.json",
            "animatic": f"designs/animatic-{key}.html",
            "captions": f"artifacts/captions-{key}.srt",
        }
        grounding_dependencies = tuple(
            DependencyRef(
                entity_type="grounding_bundle",
                entity_id=bundle["id"],
                entity_hash=bundle["content_hash"],
            )
            for bundle in bundles.values()
        )
        plan_dependencies = (
            DependencyRef(entity_type="deliverable", entity_id=deliverable["id"]),
            *(
                DependencyRef(entity_type="segment", entity_id=segment["id"])
                for segment in segments
            ),
        )

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="deterministic-animatic",
                name="Deterministic animatic renderer",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:pinaka.adapters.animatic",
                supported_operations=(
                    "render_timeline",
                    "render_animatic",
                    "render_captions",
                ),
                limits={"paid": False, "max_cost_minor": 0},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        adapter = DeterministicAnimaticAdapter()
        timeline_operation = self._animatic_operation(
            tool["id"],
            "render_timeline",
            deliverable,
            timeline,
            "timeline",
            {"media_type": "application/json", "schema": "pinaka.animatic.v1"},
        )
        _, timeline_receipt, timeline_artifact = self.run_tool_once(
            project_id,
            timeline_operation,
            adapter,
            artifact_path=paths["timeline"],
            dependencies=(*plan_dependencies, *grounding_dependencies),
        )
        if timeline_artifact is None:
            raise IntegrityError(
                "Timeline rendering did not produce an artifact: "
                f"{timeline_receipt['status']}"
            )
        timeline_dependency = DependencyRef(
            entity_type="artifact",
            entity_id=timeline_artifact["id"],
            entity_hash=timeline_artifact["content_hash"],
        )

        animatic_operation = self._animatic_operation(
            tool["id"],
            "render_animatic",
            deliverable,
            timeline,
            "html",
            {"media_type": "text/html", "width": 1080, "height": 1920},
        )
        _, animatic_receipt, animatic_artifact = self.run_tool_once(
            project_id,
            animatic_operation,
            adapter,
            artifact_path=paths["animatic"],
            artifact_kind=ArtifactKind.DESIGN,
            dependencies=(timeline_dependency,),
        )
        if animatic_artifact is None:
            raise IntegrityError(
                "Animatic rendering did not produce an artifact: "
                f"{animatic_receipt['status']}"
            )

        captions_operation = self._animatic_operation(
            tool["id"],
            "render_captions",
            deliverable,
            timeline,
            "captions",
            {"media_type": "application/x-subrip", "language": "en"},
        )
        _, captions_receipt, captions_artifact = self.run_tool_once(
            project_id,
            captions_operation,
            adapter,
            artifact_path=paths["captions"],
            dependencies=(timeline_dependency,),
        )
        if captions_artifact is None:
            raise IntegrityError(
                "Caption rendering did not produce an artifact: "
                f"{captions_receipt['status']}"
            )

        manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "bundle_key": key,
            "timeline_hash": timeline["timeline_hash"],
            "renderer_version": timeline["renderer_version"],
            "deliverable_id": deliverable["id"],
            "artifacts": {
                name: {
                    "id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                }
                for name, artifact in {
                    "timeline": timeline_artifact,
                    "animatic": animatic_artifact,
                    "captions": captions_artifact,
                }.items()
            },
        }
        manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=manifest_path,
            content=canonical_json(manifest).encode("utf-8"),
            operation={
                "type": "animatic_manifest",
                "timeline_hash": timeline["timeline_hash"],
                "renderer_version": timeline["renderer_version"],
            },
            dependencies=(
                timeline_dependency,
                DependencyRef(
                    entity_type="artifact",
                    entity_id=animatic_artifact["id"],
                    entity_hash=animatic_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=captions_artifact["id"],
                    entity_hash=captions_artifact["content_hash"],
                ),
            ),
        )

        if project.status == ProjectStatus.SOURCING:
            project = self.transition_project(
                project_id,
                ProjectStatus.ANIMATIC_READY,
                reason="Deterministic animatic, timeline and draft captions produced",
            )
        return {
            "project": project.model_dump(mode="json"),
            "timeline": timeline,
            "artifacts": {
                "timeline": timeline_artifact,
                "animatic": animatic_artifact,
                "captions": captions_artifact,
            },
            "manifest_artifact": manifest_artifact,
            "receipts": {
                "timeline": timeline_receipt,
                "animatic": animatic_receipt,
                "captions": captions_receipt,
            },
            "resumed": False,
            "next_action": self.next_action(project_id),
        }

    def qa_animatic(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError(
                "Animatic QA requires an animatic-ready project"
            )
        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            manifest_artifact, manifest, artifacts = bundle
        else:
            manifest_artifact = None
            manifest = None
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        missing = [name for name, artifact in artifacts.items() if artifact is None]
        if missing:
            raise NotFoundError(
                "Animatic QA is missing artifacts: " + ", ".join(sorted(missing))
            )
        verified = all(
            self.verify_artifact(artifact["id"]) for artifact in artifacts.values()
        )
        timeline = json.loads(self.read_artifact_text(artifacts["timeline"]["id"]))
        html_text = self.read_artifact_text(artifacts["animatic"]["id"])
        srt_text = self.read_artifact_text(artifacts["captions"]["id"])
        grounding_kinds = {
            bundle["kind"]
            for bundle in self.repository.current_grounding_bundles(project_id)
        }
        checks = run_animatic_qa(
            timeline=timeline,
            html_text=html_text,
            srt_text=srt_text,
            artifacts_verified=verified,
            grounding_kinds=grounding_kinds,
        )
        report = build_report(
            project_id=project_id,
            subject_artifact_id=artifacts["animatic"]["id"],
            checks=checks,
        )
        report_bytes = canonical_json(report.model_dump(mode="json")).encode("utf-8")
        report_hash = sha256_bytes(report_bytes)
        report_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"qa/animatic-report-{report_hash[:12]}.json",
            content=report_bytes,
            operation={
                "type": "animatic_qa",
                "overall": report.overall.value,
                "checks": len(checks),
            },
            dependencies=tuple(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                )
                for artifact in artifacts.values()
            ),
        )
        rows = [
            self.repository.record_qa_result(
                project_id=project_id,
                artifact_id=artifacts["animatic"]["id"],
                check_name=check.check,
                status=check.status.value,
                findings=check.model_dump(mode="json"),
                evidence_hash=report_hash,
            )
            for check in checks
        ]
        return {
            "project": project.model_dump(mode="json"),
            "overall": report.overall.value,
            "checks": [check.model_dump(mode="json") for check in checks],
            "report": report.model_dump(mode="json"),
            "report_artifact": report_artifact,
            "manifest_artifact": manifest_artifact,
            "animatic_bundle": manifest,
            "qa_results": rows,
        }

    def capture_animatic_frames(
        self,
        project_id: str,
        *,
        frame_count: int = 300,
        fps: int = 30,
        adapter: FrameCaptureAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError(
                "Frame capture requires an animatic-ready project"
            )
        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            _, _, artifacts = bundle
        else:
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        missing = [name for name, artifact in artifacts.items() if artifact is None]
        if missing:
            raise NotFoundError(
                "Frame capture is missing artifacts: " + ", ".join(sorted(missing))
            )
        if not all(
            self.verify_artifact(artifact["id"]) for artifact in artifacts.values()
        ):
            raise IntegrityError("Animatic artifacts failed hash verification")
        timeline = json.loads(self.read_artifact_text(artifacts["timeline"]["id"]))
        html_artifact = artifacts["animatic"]
        width = int(timeline["canvas"]["width_px"])
        height = int(timeline["canvas"]["height_px"])
        total = decimal_seconds(timeline["total_duration_seconds"])
        requested = Decimal(frame_count) / Decimal(fps)
        if requested != total:
            raise IntegrityError(
                f"{frame_count} frames at {fps}fps cover {requested}s, "
                f"but the animatic is {total}s"
            )

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="chrome-headless-capture",
                name="Chrome headless frame capture",
                integration_mode=ToolIntegrationMode.HEADLESS,
                executable_or_endpoint="chrome:--headless=new",
                supported_operations=("capture_frames",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="capture_frames",
            idempotency_key=(
                f"frames-{html_artifact['content_hash'][:12]}-{frame_count}-{fps}"
            ),
            parameters={
                "html_artifact_id": html_artifact["id"],
                "html_hash": html_artifact["content_hash"],
                "frame_count": frame_count,
                "fps": fps,
                "width": width,
                "height": height,
            },
            output_declaration={
                "media_type": "image/png-sequence",
                "frame_count": frame_count,
            },
            limits={"paid": False, "max_frames": frame_count},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError(
                    "Captured-frame receipt has no output manifest artifact"
                )
            manifest_artifact = self.repository.get_artifact(output_ids[0])
            manifest = json.loads(self.read_artifact_text(manifest_artifact["id"]))
            return {
                "project": project.model_dump(mode="json"),
                "frame_manifest": manifest,
                "manifest_artifact": manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError(
                "Frame capture outcome is unknown and requires reconciliation"
            )
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        capture = adapter or ChromeFrameCapture()
        if capture.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Capture adapter {capture.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        html_path = self.artifact_store.resolve(
            project_id, html_artifact["relative_path"]
        )
        try:
            result = capture.capture(
                html_path=html_path,
                frame_count=frame_count,
                fps=fps,
                width=width,
                height=height,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise IntegrityError(
                f"Frame capture failed: {type(error).__name__}"
            ) from error

        stored_frames: list[dict[str, Any]] = []
        for frame in result.frames:
            relative_path = (
                f"renders/frames-{html_artifact['content_hash'][:12]}/"
                f"frame-{frame.index:04d}.png"
            )
            stored = self.artifact_store.write_bytes(
                project_id, relative_path, frame.content
            )
            if stored.content_hash != frame.content_hash:
                raise IntegrityError(
                    f"Stored frame hash mismatch: {stored.relative_path}"
                )
            stored_frames.append(
                {
                    "index": frame.index,
                    "time_seconds": frame.time_seconds,
                    "relative_path": stored.relative_path,
                    "content_hash": stored.content_hash,
                    "size_bytes": stored.size_bytes,
                }
            )

        manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "html_artifact_id": html_artifact["id"],
            "html_hash": html_artifact["content_hash"],
            "timeline_hash": timeline["timeline_hash"],
            "frame_count": frame_count,
            "fps": fps,
            "duration_seconds": str(total),
            "width_px": width,
            "height_px": height,
            "browser": result.renderer_version,
            "command_hash": result.command_hash,
            "frames": stored_frames,
        }
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=f"renders/frame-manifest-{manifest_hash[:12]}.json",
            content=manifest_bytes,
            operation={
                "type": "frame_capture",
                "frame_count": frame_count,
                "fps": fps,
                "browser": result.renderer_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=html_artifact["id"],
                    entity_hash=html_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifacts["timeline"]["id"],
                    entity_hash=artifacts["timeline"]["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(manifest_artifact["id"],),
                response_hash=manifest_hash,
                details={
                    "frame_count": frame_count,
                    "fps": fps,
                    "browser": result.renderer_version,
                    "width_px": width,
                    "height_px": height,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "frame_manifest": manifest,
            "manifest_artifact": manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

    def encode_silent_animatic(
        self,
        project_id: str,
        *,
        adapter: VideoEncodeAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError(
                "Silent encode requires an animatic-ready project"
            )
        manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("renders/frame-manifest-")
        ]
        if not manifests:
            raise NotFoundError("No captured frame manifest exists for this project")
        frame_manifest_artifact = max(manifests, key=lambda item: item["created_at"])
        if not self.verify_artifact(frame_manifest_artifact["id"]):
            raise IntegrityError("Frame manifest failed hash verification")
        frame_manifest = json.loads(
            self.read_artifact_text(frame_manifest_artifact["id"])
        )
        if frame_manifest.get("project_id") != project_id:
            raise IntegrityError("Frame manifest belongs to another project")

        frame_dir: Path | None = None
        for frame in frame_manifest["frames"]:
            path = self.artifact_store.resolve(project_id, frame["relative_path"])
            if not path.is_file() or sha256_file(path) != frame["content_hash"]:
                raise IntegrityError(
                    f"Captured frame failed hash verification: {frame['relative_path']}"
                )
            if frame_dir is None:
                frame_dir = path.parent
            elif path.parent != frame_dir:
                raise IntegrityError("Captured frames are not in one directory")
        if frame_dir is None:
            raise IntegrityError("Frame manifest contains no frames")

        frame_count = int(frame_manifest["frame_count"])
        fps = int(frame_manifest["fps"])
        width = int(frame_manifest["width_px"])
        height = int(frame_manifest["height_px"])
        duration = decimal_seconds(frame_manifest["duration_seconds"])

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="ffmpeg-h264",
                name="FFmpeg H.264 encoder",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:imageio_ffmpeg",
                supported_operations=("encode_silent_master",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="encode_silent_master",
            idempotency_key=(
                f"encode-silent-{frame_manifest_artifact['content_hash'][:12]}"
            ),
            parameters={
                "frame_manifest_artifact_id": frame_manifest_artifact["id"],
                "frame_manifest_hash": frame_manifest_artifact["content_hash"],
                "frame_count": frame_count,
                "fps": fps,
                "width_px": width,
                "height_px": height,
            },
            output_declaration={
                "media_type": "video/mp4",
                "codec": "h264",
                "audio": False,
            },
            limits={"paid": False, "max_seconds": str(duration)},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError("Encode receipt has no output artifacts")
            video_artifact = self.repository.get_artifact(output_ids[0])
            encode_manifest_artifact = (
                self.repository.get_artifact(output_ids[1])
                if len(output_ids) > 1
                else None
            )
            return {
                "project": project.model_dump(mode="json"),
                "video_artifact": video_artifact,
                "encode_manifest_artifact": encode_manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError("Encode outcome is unknown and requires reconciliation")
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        encoder = adapter or FFmpegEncodeAdapter()
        if encoder.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Encode adapter {encoder.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        try:
            result = encoder.encode(
                frames_dir=frame_dir,
                frame_count=frame_count,
                fps=fps,
                width=width,
                height=height,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise IntegrityError(
                f"Silent encode failed: {type(error).__name__}"
            ) from error

        metadata = result.metadata
        expected = {
            "width_px": width,
            "height_px": height,
            "fps": float(fps),
        }
        observed = {
            "width_px": metadata.get("width_px"),
            "height_px": metadata.get("height_px"),
            "fps": metadata.get("fps"),
        }
        if observed != expected:
            raise IntegrityError(
                f"Encoded stream dimensions/fps {observed} do not match {expected}"
            )
        if metadata.get("codec") != "h264" or metadata.get("has_audio"):
            raise IntegrityError("Encoded master is not a silent H.264 video")
        if abs(float(metadata.get("duration_seconds", 0)) - float(duration)) > 0.05:
            raise IntegrityError("Encoded duration does not match the frame timeline")
        if int(metadata.get("size_bytes", 0)) != len(result.content):
            raise IntegrityError("Encoded byte size does not match metadata")

        video_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/animatic-silent-{result.content_hash[:12]}.mp4",
            content=result.content,
            operation={
                "type": "encode_silent_master",
                "codec": "h264",
                "fps": fps,
                "frame_count": frame_count,
                "encoder": result.encoder_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=frame_manifest_artifact["id"],
                    entity_hash=frame_manifest_artifact["content_hash"],
                ),
            ),
        )
        encode_manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "frame_manifest_artifact_id": frame_manifest_artifact["id"],
            "frame_manifest_hash": frame_manifest_artifact["content_hash"],
            "video_artifact_id": video_artifact["id"],
            "video_hash": video_artifact["content_hash"],
            "codec": "h264",
            "audio": False,
            "frame_count": frame_count,
            "fps": fps,
            "duration_seconds": str(duration),
            "width_px": width,
            "height_px": height,
            "metadata": metadata,
            "encoder_version": result.encoder_version,
            "command_hash": result.command_hash,
            "licence_note": (
                "FFmpeg GPL-enabled build with libx264. Internal production use; "
                "review distribution obligations before distributing the binary."
            ),
        }
        encode_manifest_bytes = canonical_json(encode_manifest).encode("utf-8")
        encode_manifest_hash = sha256_bytes(encode_manifest_bytes)
        encode_manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=(f"renders/encode-manifest-{encode_manifest_hash[:12]}.json"),
            content=encode_manifest_bytes,
            operation={
                "type": "silent_encode_manifest",
                "codec": "h264",
                "audio": False,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video_artifact["id"],
                    entity_hash=video_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=frame_manifest_artifact["id"],
                    entity_hash=frame_manifest_artifact["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(
                    video_artifact["id"],
                    encode_manifest_artifact["id"],
                ),
                response_hash=encode_manifest_hash,
                details={
                    "codec": "h264",
                    "audio": False,
                    "frame_count": frame_count,
                    "fps": fps,
                    "duration_seconds": str(duration),
                    "encoder": result.encoder_version,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "video_artifact": video_artifact,
            "encode_manifest": encode_manifest,
            "encode_manifest_artifact": encode_manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

    def synthesize_narration(
        self,
        project_id: str,
        *,
        voice: str = "af_heart",
        speed: float = 1.0,
        adapter: NarrationAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError("Narration requires an animatic-ready project")
        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            _, _, artifacts = bundle
        else:
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        missing = [name for name, artifact in artifacts.items() if artifact is None]
        if missing:
            raise NotFoundError(
                "Narration is missing animatic artifacts: " + ", ".join(sorted(missing))
            )
        timeline = json.loads(self.read_artifact_text(artifacts["timeline"]["id"]))
        text = str(timeline.get("voiceover") or "").strip()
        if not text:
            raise IntegrityError("The approved timeline has no voiceover text")
        total = decimal_seconds(timeline["total_duration_seconds"])
        text_hash = sha256_bytes(text.encode("utf-8"))
        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="kokoro-tts",
                name="Kokoro local narration",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:kokoro_onnx",
                supported_operations=("synthesize_narration",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="synthesize_narration",
            idempotency_key=f"narration-{text_hash[:12]}-{voice}-{speed}",
            parameters={
                "text": text,
                "text_hash": text_hash,
                "voice": voice,
                "speed": speed,
            },
            output_declaration={"media_type": "audio/wav", "language": "en"},
            limits={"paid": False, "max_seconds": str(total)},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError("Narration receipt has no output artifacts")
            wav_artifact = self.repository.get_artifact(output_ids[0])
            manifest_artifact = (
                self.repository.get_artifact(output_ids[1])
                if len(output_ids) > 1
                else None
            )
            return {
                "project": project.model_dump(mode="json"),
                "narration_artifact": wav_artifact,
                "narration_manifest_artifact": manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError(
                "Narration outcome is unknown and requires reconciliation"
            )
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        narrator = adapter or KokoroNarrationAdapter.from_default_paths(
            voice=voice, speed=speed
        )
        if narrator.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Narration adapter {narrator.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        try:
            result = narrator.synthesize(text)
        except (OSError, RuntimeError, ValueError) as error:
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise IntegrityError(
                f"Narration synthesis failed: {type(error).__name__}"
            ) from error
        if result.duration_seconds > float(total) - 0.15:
            raise IntegrityError(
                f"Narration is {result.duration_seconds:.3f}s, leaving less than "
                f"the required 0.15s tail in a {total}s video"
            )
        wav_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.AUDIO,
            relative_path=f"audio/narration-{result.content_hash[:12]}.wav",
            content=result.content,
            operation={
                "type": "kokoro_narration",
                "voice": result.voice,
                "speed": result.speed,
                "model": result.model_name,
                "sample_rate": result.sample_rate,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifacts["timeline"]["id"],
                    entity_hash=artifacts["timeline"]["content_hash"],
                ),
            ),
        )
        narration_manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "text": text,
            "text_hash": result.text_hash,
            "voice": result.voice,
            "speed": result.speed,
            "sample_rate": result.sample_rate,
            "duration_seconds": result.duration_seconds,
            "model_name": result.model_name,
            "model_sha256": result.model_sha256,
            "voices_sha256": result.voices_sha256,
            "licence_note": result.licence_note,
            "command_hash": result.command_hash,
            "narration_artifact_id": wav_artifact["id"],
            "narration_hash": wav_artifact["content_hash"],
        }
        manifest_bytes = canonical_json(narration_manifest).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=(f"audio/narration-manifest-{manifest_hash[:12]}.json"),
            content=manifest_bytes,
            operation={
                "type": "narration_manifest",
                "voice": result.voice,
                "speed": result.speed,
                "model": result.model_name,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=wav_artifact["id"],
                    entity_hash=wav_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifacts["timeline"]["id"],
                    entity_hash=artifacts["timeline"]["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(wav_artifact["id"], manifest_artifact["id"]),
                response_hash=manifest_hash,
                details={
                    "voice": result.voice,
                    "speed": result.speed,
                    "duration_seconds": result.duration_seconds,
                    "sample_rate": result.sample_rate,
                    "model": result.model_name,
                    "paid": False,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "narration_artifact": wav_artifact,
            "narration_manifest": narration_manifest,
            "narration_manifest_artifact": manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

    @staticmethod
    def _validate_mux_result(result: Any, duration: Decimal) -> None:
        metadata = result.metadata
        if abs(float(metadata.get("duration_seconds", 0)) - float(duration)) > 0.05:
            raise IntegrityError("Muxed duration does not match the approved timeline")
        if (
            metadata.get("video_codec") != "h264"
            or metadata.get("width_px") != 1080
            or metadata.get("height_px") != 1920
            or float(metadata.get("fps", 0)) != 30.0
        ):
            raise IntegrityError(
                "Muxed video stream does not match the approved profile"
            )
        if (
            metadata.get("audio_codec") != "aac"
            or metadata.get("audio_sample_rate") != 48000
            or metadata.get("audio_channels") != "stereo"
        ):
            raise IntegrityError(
                "Muxed audio stream does not match the approved profile"
            )
        try:
            integrated = float(result.loudness.get("input_i"))
            true_peak = float(result.loudness.get("input_tp"))
        except (TypeError, ValueError) as error:
            raise IntegrityError("Muxed loudness measurement is invalid") from error
        if abs(integrated + 16.0) > 1.0:
            raise IntegrityError(
                f"Muxed loudness {integrated:.1f} LUFS is outside the -16 LUFS target"
            )
        if true_peak > -1.0:
            raise IntegrityError(
                f"Muxed true peak {true_peak:.1f} dBTP exceeds the -1.0 dBTP ceiling"
            )

    def mux_voiced_animatic(
        self,
        project_id: str,
        *,
        adapter: MuxAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError("Voiced mux requires an animatic-ready project")
        encode_manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("renders/encode-manifest-")
        ]
        narration_manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("audio/narration-manifest-")
        ]
        if not encode_manifests:
            raise NotFoundError("No silent encode manifest exists for this project")
        if not narration_manifests:
            raise NotFoundError("No narration manifest exists for this project")
        encode_manifest_artifact = max(
            encode_manifests, key=lambda item: item["created_at"]
        )
        narration_manifest_artifact = max(
            narration_manifests, key=lambda item: item["created_at"]
        )
        if not self.verify_artifact(encode_manifest_artifact["id"]):
            raise IntegrityError("Silent encode manifest failed hash verification")
        if not self.verify_artifact(narration_manifest_artifact["id"]):
            raise IntegrityError("Narration manifest failed hash verification")
        encode_manifest = json.loads(
            self.read_artifact_text(encode_manifest_artifact["id"])
        )
        narration_manifest = json.loads(
            self.read_artifact_text(narration_manifest_artifact["id"])
        )
        video_artifact = self.repository.get_artifact(
            encode_manifest["video_artifact_id"]
        )
        narration_artifact = self.repository.get_artifact(
            narration_manifest["narration_artifact_id"]
        )
        if not self.verify_artifact(video_artifact["id"]):
            raise IntegrityError("Silent video failed hash verification")
        if not self.verify_artifact(narration_artifact["id"]):
            raise IntegrityError("Narration WAV failed hash verification")
        duration = decimal_seconds(encode_manifest["duration_seconds"])

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="ffmpeg-mux",
                name="FFmpeg narration mux",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:imageio_ffmpeg",
                supported_operations=("mux_narration",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="mux_narration",
            idempotency_key=(
                f"mux-{video_artifact['content_hash'][:12]}-"
                f"{narration_artifact['content_hash'][:12]}"
            ),
            parameters={
                "video_artifact_id": video_artifact["id"],
                "video_hash": video_artifact["content_hash"],
                "narration_artifact_id": narration_artifact["id"],
                "narration_hash": narration_artifact["content_hash"],
                "duration_seconds": str(duration),
            },
            output_declaration={
                "media_type": "video/mp4",
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            limits={"paid": False, "max_seconds": str(duration)},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError("Mux receipt has no output artifacts")
            voiced_artifact = self.repository.get_artifact(output_ids[0])
            mux_manifest_artifact = (
                self.repository.get_artifact(output_ids[1])
                if len(output_ids) > 1
                else None
            )
            return {
                "project": project.model_dump(mode="json"),
                "video_artifact": voiced_artifact,
                "mux_manifest_artifact": mux_manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError("Mux outcome is unknown and requires reconciliation")
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        muxer = adapter or FFmpegMuxAdapter()
        if muxer.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Mux adapter {muxer.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        video_path = self.artifact_store.resolve(
            project_id, video_artifact["relative_path"]
        )
        audio_path = self.artifact_store.resolve(
            project_id, narration_artifact["relative_path"]
        )
        try:
            result = muxer.mux(
                video_path=video_path,
                audio_path=audio_path,
                duration_seconds=float(duration),
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise IntegrityError(
                f"Voiced mux failed: {type(error).__name__}"
            ) from error

        try:
            self._validate_mux_result(result, duration)
        except IntegrityError as error:
            # The adapter already produced a file; record the rejection so the
            # operation does not stay in "running" and block reconciliation.
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise

        metadata = result.metadata
        integrated = float(result.loudness.get("input_i"))
        true_peak = float(result.loudness.get("input_tp"))

        voiced_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=(f"renders/animatic-voiced-{result.content_hash[:12]}.mp4"),
            content=result.content,
            operation={
                "type": "mux_narration",
                "video_codec": "h264",
                "audio_codec": "aac",
                "duration_seconds": str(duration),
                "voice": narration_manifest.get("voice"),
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video_artifact["id"],
                    entity_hash=video_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration_artifact["id"],
                    entity_hash=narration_artifact["content_hash"],
                ),
            ),
        )
        mux_manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "video_artifact_id": voiced_artifact["id"],
            "video_hash": voiced_artifact["content_hash"],
            "silent_video_artifact_id": video_artifact["id"],
            "silent_video_hash": video_artifact["content_hash"],
            "narration_artifact_id": narration_artifact["id"],
            "narration_hash": narration_artifact["content_hash"],
            "narration_text_hash": narration_manifest.get("text_hash"),
            "narration_duration_seconds": narration_manifest.get("duration_seconds"),
            "narration_voice": narration_manifest.get("voice"),
            "narration_speed": narration_manifest.get("speed"),
            "duration_seconds": str(duration),
            "metadata": metadata,
            "loudness": result.loudness,
            "command_hash": result.command_hash,
            "encoder_version": result.encoder_version,
            "licence_note": (
                "FFmpeg GPL-enabled build with libx264 and AAC. Internal production "
                "use; review distribution obligations before distributing the binary."
            ),
        }
        manifest_bytes = canonical_json(mux_manifest).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        mux_manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=f"renders/mux-manifest-{manifest_hash[:12]}.json",
            content=manifest_bytes,
            operation={
                "type": "voiced_mux_manifest",
                "duration_seconds": str(duration),
                "voice": narration_manifest.get("voice"),
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=voiced_artifact["id"],
                    entity_hash=voiced_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration_artifact["id"],
                    entity_hash=narration_artifact["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(
                    voiced_artifact["id"],
                    mux_manifest_artifact["id"],
                ),
                response_hash=manifest_hash,
                details={
                    "duration_seconds": str(duration),
                    "voice": narration_manifest.get("voice"),
                    "integrated_lufs": integrated,
                    "true_peak_dbtp": true_peak,
                    "audio_codec": "aac",
                    "paid": False,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "video_artifact": voiced_artifact,
            "mux_manifest": mux_manifest,
            "mux_manifest_artifact": mux_manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

    def qa_voiced_animatic(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
            ProjectStatus.FINAL_CANDIDATE,
        }:
            raise ApprovalRequiredError(
                "Voiced QA requires an animatic-ready or final-candidate project"
            )
        mux_manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("renders/mux-manifest-")
        ]
        if not mux_manifests:
            raise NotFoundError("No voiced mux manifest exists for this project")
        mux_manifest_artifact = max(mux_manifests, key=lambda item: item["created_at"])
        if not self.verify_artifact(mux_manifest_artifact["id"]):
            raise IntegrityError("Mux manifest failed hash verification")
        mux_manifest = json.loads(self.read_artifact_text(mux_manifest_artifact["id"]))
        video_artifact = self.repository.get_artifact(mux_manifest["video_artifact_id"])
        narration_artifact = self.repository.get_artifact(
            mux_manifest["narration_artifact_id"]
        )
        video_verified = self.verify_artifact(video_artifact["id"])
        narration_verified = self.verify_artifact(narration_artifact["id"])

        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            _, _, animatic_artifacts = bundle
        else:
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            animatic_artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        if animatic_artifacts["timeline"] is None:
            raise NotFoundError("Voiced QA requires the animatic timeline")
        timeline = json.loads(
            self.read_artifact_text(animatic_artifacts["timeline"]["id"])
        )
        expected_text_hash = sha256_bytes(
            str(timeline.get("voiceover") or "").encode("utf-8")
        )
        checks = run_voiced_animatic_qa(
            mux_manifest=mux_manifest,
            video_verified=video_verified,
            narration_verified=narration_verified,
            expected_text_hash=expected_text_hash,
        )
        report = build_report(
            project_id=project_id,
            subject_artifact_id=video_artifact["id"],
            checks=checks,
        )
        report_bytes = canonical_json(report.model_dump(mode="json")).encode("utf-8")
        report_hash = sha256_bytes(report_bytes)
        report_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"qa/voiced-report-{report_hash[:12]}.json",
            content=report_bytes,
            operation={
                "type": "voiced_animatic_qa",
                "overall": report.overall.value,
                "checks": len(checks),
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video_artifact["id"],
                    entity_hash=video_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration_artifact["id"],
                    entity_hash=narration_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=mux_manifest_artifact["id"],
                    entity_hash=mux_manifest_artifact["content_hash"],
                ),
            ),
        )
        rows = [
            self.repository.record_qa_result(
                project_id=project_id,
                artifact_id=video_artifact["id"],
                check_name=check.check,
                status=check.status.value,
                findings=check.model_dump(mode="json"),
                evidence_hash=report_hash,
            )
            for check in checks
        ]
        return {
            "project": project.model_dump(mode="json"),
            "overall": report.overall.value,
            "checks": [check.model_dump(mode="json") for check in checks],
            "report": report.model_dump(mode="json"),
            "report_artifact": report_artifact,
            "video_artifact": video_artifact,
            "qa_results": rows,
        }

    def reserve_job(self, project_id: str, request: JobRequest) -> JobRecord:
        self._reject_sensitive_payload(request.model_dump(mode="json"))
        if request.required_approval_stage:
            approval = self.repository.active_approval(
                project_id, request.required_approval_stage.value
            )
            if not approval:
                raise ApprovalRequiredError(
                    f"Active {request.required_approval_stage.value} approval is required"
                )
        request_hash = sha256_json(request.model_dump(mode="json"))
        return self.repository.reserve_job(project_id, request, request_hash)

    @classmethod
    def _reject_sensitive_payload(cls, value: Any, path: str = "request") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                    raise IntegrityError(
                        f"Sensitive field is forbidden in persisted payloads: {path}.{key}"
                    )
                cls._reject_sensitive_payload(child, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                cls._reject_sensitive_payload(child, f"{path}[{index}]")

    def mark_job_submitting(self, job_id: str) -> JobRecord:
        return self.repository.update_job_status(
            job_id,
            expected={JobStatus.BUDGET_RESERVED},
            target=JobStatus.SUBMITTING,
        )

    def record_job_submitted(
        self, job_id: str, provider_job_id: str, response: dict[str, Any]
    ) -> JobRecord:
        self._reject_sensitive_payload(response, "provider_response")
        return self.repository.update_job_status(
            job_id,
            expected={JobStatus.SUBMITTING, JobStatus.SUBMISSION_UNKNOWN},
            target=JobStatus.SUBMITTED,
            provider_job_id=provider_job_id,
            response=response,
        )

    def mark_job_running(
        self, job_id: str, response: dict[str, Any] | None = None
    ) -> JobRecord:
        if response is not None:
            self._reject_sensitive_payload(response, "provider_response")
        return self.repository.update_job_status(
            job_id,
            expected={JobStatus.SUBMITTED, JobStatus.SUBMISSION_UNKNOWN},
            target=JobStatus.RUNNING,
            response=response,
        )

    def mark_submission_unknown(self, job_id: str, error: dict[str, Any]) -> JobRecord:
        self._reject_sensitive_payload(error, "provider_error")
        return self.repository.update_job_status(
            job_id,
            expected={JobStatus.SUBMITTING},
            target=JobStatus.SUBMISSION_UNKNOWN,
            error=error,
        )

    def settle_job(
        self,
        job_id: str,
        *,
        target: JobStatus,
        actual_cost: Money,
        response: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        asset_artifact_id: str | None = None,
        approved_variance_minor: int | None = None,
    ) -> JobRecord:
        if response is not None:
            self._reject_sensitive_payload(response, "provider_response")
        if error is not None:
            self._reject_sensitive_payload(error, "provider_error")
        return self.repository.settle_job(
            job_id,
            target=target,
            actual_cost=actual_cost,
            response=response,
            error=error,
            asset_artifact_id=asset_artifact_id,
            approved_variance_minor=approved_variance_minor,
        )

    def settle_budget_item(
        self,
        project_id: str,
        job_id: str,
        *,
        actual_cost: Money,
        requested_by: str | None = None,
        reason: str | None = None,
        asset_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        """Settle a planned job, raising an approval request when over tolerance."""

        job = self.repository.get_job(job_id)
        if job.project_id != project_id:
            raise ConflictError("Job belongs to a different project")
        try:
            settled = self.settle_job(
                job_id,
                target=JobStatus.SUCCEEDED,
                actual_cost=actual_cost,
                asset_artifact_id=asset_artifact_id,
            )
        except VarianceApprovalRequiredError as error:
            request = self.repository.record_variance_request(
                project_id,
                plan_id=error.plan_id,
                item_id=error.item_id,
                job_id=error.job_id,
                estimated_minor=error.estimated_minor,
                actual_minor=error.actual_minor,
                allowed_minor=error.allowed_minor,
                currency=error.currency,
                requested_by=requested_by,
                reason=reason,
            )
            return {
                "status": "approval_required",
                "message": (
                    "Actual cost is beyond the plan tolerance. Approve or reject "
                    f"request {request['id']} to proceed."
                ),
                "request": request,
                "summary": self.budget_summary(project_id),
            }
        return {
            "status": "settled",
            "job": settled.model_dump(mode="json"),
            "summary": self.budget_summary(project_id),
        }

    def variance_requests(self, project_id: str) -> list[dict[str, Any]]:
        return self.repository.pending_variance_requests(project_id)

    def raise_project_budget(
        self, project_id: str, request: BudgetRaiseCreate
    ) -> dict[str, Any]:
        """Human-authorized increase of the project budget ceiling."""

        project = self.repository.get_project(project_id)
        if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
            raise ConflictError(
                f"Cannot raise the budget of a {project.status.value} project"
            )
        self._reject_sensitive_payload(request.model_dump(mode="json"), "budget_raise")
        updated = self.repository.raise_project_budget(
            project_id,
            new_max=request.new_max,
            raised_by=request.raised_by,
            reason=request.reason,
        )
        return {"project": updated, "summary": self.budget_summary(project_id)}

    def reconcile_overage(
        self, project_id: str, request: OverageReconcileCreate
    ) -> dict[str, Any]:
        """Record a human-accepted provider overrun and settle the job."""

        job = self.repository.get_job(request.job_id)
        if job.project_id != project_id:
            raise ConflictError("Job belongs to a different project")
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise ConflictError(f"Job is already terminal: {job.status.value}")
        if job.estimated_cost.currency != request.actual_cost.currency:
            raise IntegrityError("Reconciled cost currency must match the job")
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "overage_reconcile"
        )
        settled = self.settle_job(
            request.job_id,
            target=JobStatus.SUCCEEDED,
            actual_cost=request.actual_cost,
            approved_variance_minor=request.actual_cost.minor,
        )
        self.repository.record_overage_reconciliation(
            project_id,
            job_id=request.job_id,
            actual_cost=request.actual_cost,
            reconciled_by=request.reconciled_by,
            reason=request.reason,
        )
        return {
            "job": settled.model_dump(mode="json"),
            "summary": self.budget_summary(project_id),
        }

    def asset_costs(self, project_id: str) -> dict[str, Any]:
        """Per-asset generation cost ledger with project and provider totals."""

        project = self.repository.get_project(project_id)
        costs = self.repository.list_asset_costs(project_id)
        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        currency = project.max_budget.currency
        rows = []
        by_provider: dict[str, dict[str, int]] = {}
        total_estimated = 0
        total_actual = 0
        for cost in costs:
            slot = slots.get(cost["slot_id"]) if cost["slot_id"] else None
            row = {
                **cost,
                "slot_key": slot["stable_key"] if slot else None,
                "slot_role": slot["role"] if slot else None,
            }
            rows.append(row)
            total_estimated += cost["estimated_minor"]
            total_actual += cost["actual_minor"]
            bucket = by_provider.setdefault(
                cost["provider"], {"estimated_minor": 0, "actual_minor": 0, "count": 0}
            )
            bucket["estimated_minor"] += cost["estimated_minor"]
            bucket["actual_minor"] += cost["actual_minor"]
            bucket["count"] += 1
        variance = total_actual - total_estimated
        return {
            "project_id": project_id,
            "currency": currency,
            "count": len(rows),
            "total_estimated": Money.from_minor(total_estimated, currency).model_dump(
                mode="json"
            ),
            "total_actual": Money.from_minor(total_actual, currency).model_dump(
                mode="json"
            ),
            "total_variance": Money.from_minor(abs(variance), currency).model_dump(
                mode="json"
            ),
            "total_variance_minor": variance,
            "total_variance_direction": (
                "over" if variance > 0 else "under" if variance < 0 else "even"
            ),
            "by_provider": {
                provider: {
                    "count": values["count"],
                    "estimated": Money.from_minor(
                        values["estimated_minor"], currency
                    ).model_dump(mode="json"),
                    "actual": Money.from_minor(
                        values["actual_minor"], currency
                    ).model_dump(mode="json"),
                }
                for provider, values in sorted(by_provider.items())
            },
            "assets": rows,
        }

    def decide_variance(
        self, project_id: str, request: VarianceDecisionCreate
    ) -> dict[str, Any]:
        """Apply a human decision to a pending budget variance request."""

        variance = self.repository.get_variance_request(request.request_id)
        if variance["project_id"] != project_id:
            raise ConflictError("Variance request belongs to a different project")
        if variance["status"] != "pending":
            raise ConflictError(f"Variance request is already {variance['status']}")
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "variance_decision"
        )
        job = self.repository.get_job(variance["job_id"])
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise ConflictError(f"Job is already terminal: {job.status.value}")
        actual = Money.from_minor(variance["actual_minor"], variance["currency"])
        if variance["kind"] == "studio_run":
            if request.decision == "approve":
                decision = self.repository.decide_variance_request(
                    variance["id"],
                    decision="approved",
                    decided_by=request.decided_by,
                    decision_reason=request.reason,
                )
                return {
                    "decision": decision,
                    "job": job.model_dump(mode="json"),
                    "next": "run studio-run again to collect and settle the run",
                    "summary": self.budget_summary(project_id),
                }
            self.repository.decide_variance_request(
                variance["id"],
                decision="rejected",
                decided_by=request.decided_by,
                decision_reason=request.reason,
            )
            cancelled = self.import_studio_result(
                StudioResultPacketImport(
                    operation_id=variance["operation_id"],
                    status=ToolOperationStatus.CANCELLED,
                    elapsed_ms=0,
                    actual_cost=Money(amount=0, currency=variance["currency"]),
                    details={
                        "integration": "openmontage",
                        "cancelled_reason": request.reason,
                    },
                )
            )
            production = self.repository.get_studio_production(variance["operation_id"])
            if production:
                self.repository.update_studio_production(
                    production["id"], status="cancelled"
                )
            return {
                "decision": self.repository.get_variance_request(variance["id"]),
                "job": cancelled["receipt"],
                "summary": self.budget_summary(project_id),
            }
        if request.decision == "approve":
            settled = self.repository.settle_job(
                variance["job_id"],
                target=JobStatus.SUCCEEDED,
                actual_cost=actual,
                approved_variance_minor=variance["actual_minor"],
            )
            decision = self.repository.decide_variance_request(
                variance["id"],
                decision="approved",
                decided_by=request.decided_by,
                decision_reason=request.reason,
            )
            return {
                "decision": decision,
                "job": settled.model_dump(mode="json"),
                "summary": self.budget_summary(project_id),
            }
        self.repository.skip_budget_item(
            variance["item_id"], reason=f"Variance rejected: {request.reason}"
        )
        cancelled = self.repository.settle_job(
            variance["job_id"],
            target=JobStatus.CANCELLED,
            actual_cost=Money(amount=0, currency=variance["currency"]),
        )
        decision = self.repository.decide_variance_request(
            variance["id"],
            decision="rejected",
            decided_by=request.decided_by,
            decision_reason=request.reason,
        )
        return {
            "decision": decision,
            "job": cancelled.model_dump(mode="json"),
            "reported_actual": variance["actual"],
            "summary": self.budget_summary(project_id),
        }

    def run_adapter_once(
        self,
        project_id: str,
        request: JobRequest,
        adapter: GenerationAdapter,
        *,
        artifact_path: str,
        budget_plan_item: tuple[str, str] | None = None,
    ) -> tuple[JobRecord, dict[str, Any] | None]:
        if request.provider != adapter.provider_name:
            raise IntegrityError(
                f"Request provider {request.provider!r} does not match adapter "
                f"{adapter.provider_name!r}"
            )
        if budget_plan_item:
            planned_plan_id, item_key = budget_plan_item
            reserved = self.reserve_budget_item(
                project_id, planned_plan_id, item_key, request
            )
            job = JobRecord.model_validate(reserved["job"])
        else:
            planned_plan_id = None
            job = self.reserve_job(project_id, request)
        if job.status == JobStatus.SUCCEEDED:
            return job, None
        if job.status == JobStatus.BUDGET_RESERVED:
            job = self.mark_job_submitting(job.id)
            try:
                submission = adapter.submit(request)
            except Exception as error:
                self.mark_submission_unknown(
                    job.id,
                    {"type": type(error).__name__, "message": str(error)},
                )
                raise
            job = self.record_job_submitted(
                job.id, submission.provider_job_id, submission.raw
            )
        if job.status not in {JobStatus.SUBMITTED, JobStatus.RUNNING}:
            raise ConflictError(
                f"Job {job.id} requires reconciliation before polling: {job.status.value}"
            )
        result = adapter.poll(job.provider_job_id or "", request)
        if (
            result.status != JobStatus.SUCCEEDED
            or result.output_bytes is None
            or not result.actual_cost
        ):
            raise ConflictError(
                "Single-pass adapter did not return a complete successful result"
            )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.GENERATED,
            relative_path=artifact_path,
            content=result.output_bytes,
            operation={
                "type": "generation",
                "provider": request.provider,
                "job_id": job.id,
            },
            dependencies=(DependencyRef(entity_type="job", entity_id=job.id),),
        )
        settled = self.settle_job(
            job.id,
            target=JobStatus.SUCCEEDED,
            actual_cost=result.actual_cost,
            response=result.raw,
            asset_artifact_id=artifact["id"] if budget_plan_item else None,
        )
        return settled, artifact

    def next_action(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        actions = {
            ProjectStatus.DRAFT: (
                "plan",
                "Record deliverables, segments, grounding needs, provider policy and schedule.",
            ),
            ProjectStatus.PLANNED: (
                "approve_brief",
                "Obtain human approval for the normalized brief and its budget plan.",
            ),
            ProjectStatus.BRIEF_APPROVED: (
                "ground",
                "Add typed brand, claims and source grounding bundles.",
            ),
            ProjectStatus.FACTS_LOCKED: (
                "begin_sourcing",
                "Legacy facts are locked; transition to sourcing or add typed grounding.",
            ),
            ProjectStatus.GROUNDING_LOCKED: (
                "begin_sourcing",
                "Transition to sourcing and evaluate every candidate before import.",
            ),
            ProjectStatus.SOURCING: (
                "prepare_animatic",
                "Complete rights-cleared sourcing and create an editable animatic.",
            ),
            ProjectStatus.ANIMATIC_READY: (
                "approve_animatic",
                "Human-review timing, narration, captions and generation allowances.",
            ),
            ProjectStatus.ANIMATIC_APPROVED: (
                "produce_final",
                "Produce the final candidate with governed tool operations.",
            ),
            ProjectStatus.FINAL_CANDIDATE: (
                "approve_final",
                "Run QA and approve the exact final output hash.",
            ),
            ProjectStatus.FINAL_APPROVED: (
                "archive",
                "Write the immutable archive manifest and close the project.",
            ),
            ProjectStatus.ARCHIVED: ("complete", "Project is archived."),
            ProjectStatus.CANCELLED: ("none", "Project is cancelled."),
        }
        action, detail = actions[project.status]
        return {
            "project_id": project.id,
            "status": project.status.value,
            "action": action,
            "detail": detail,
        }

    def archive_project(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status == ProjectStatus.ARCHIVED:
            archive = self.repository.get_archive(project_id)
            if not archive:
                raise IntegrityError("Archived project is missing its archive record")
            return archive
        if project.status != ProjectStatus.FINAL_APPROVED:
            raise ApprovalRequiredError(
                "Project must have an exact-hash final approval before archive"
            )
        final_approval = self.repository.active_approval(
            project_id, ApprovalStage.FINAL.value
        )
        if not final_approval or not final_approval["output_hash"]:
            raise ApprovalRequiredError("Active exact-hash final approval is required")
        manifest = {
            "schema_version": 2,
            "project": project.model_dump(mode="json"),
            "final_approval": final_approval,
            "approvals": {
                "brief": self.repository.active_approval(
                    project_id, ApprovalStage.BRIEF.value
                ),
                "sourcing": self.repository.active_approval(
                    project_id, ApprovalStage.SOURCING.value
                ),
                "animatic": self.repository.active_approval(
                    project_id, ApprovalStage.ANIMATIC.value
                ),
                "final": final_approval,
            },
            "grounding_bundles": self.repository.current_grounding_bundles(project_id),
            "legacy_fact_set": self.repository.current_fact_set(project_id),
            "deliverables": [
                item.model_dump(mode="json")
                for item in self.repository.list_deliverables(project_id)
            ],
            "segments": self.repository.list_segments(project_id),
            "artifacts": self.repository.list_artifacts(project_id),
            "tool_operations": self.repository.list_tool_operations(project_id),
            "qa_results": self.repository.list_qa_results(project_id),
            "budget_ledger": self.repository.budget_ledger(project_id),
        }
        content = canonical_json(manifest).encode("utf-8")
        stored = self.artifact_store.write_bytes(
            project_id, "archive/manifest.json", content
        )
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            artifact = existing
        else:
            artifact = self.repository.register_artifact(
                project_id,
                ArtifactCreate(
                    kind=ArtifactKind.ARCHIVE,
                    relative_path=stored.relative_path,
                    content_hash=stored.content_hash,
                    operation={
                        "type": "archive_manifest",
                        "size_bytes": stored.size_bytes,
                        "final_output_hash": final_approval["output_hash"],
                    },
                    immutable=True,
                    dependencies=(
                        DependencyRef(
                            entity_type="approval",
                            entity_id=final_approval["id"],
                            entity_hash=final_approval["subject_hash"],
                        ),
                    ),
                ),
            )
        return self.repository.register_archive(
            project_id, artifact["id"], stored.content_hash
        )

    def check_capabilities(self) -> list[dict[str, Any]]:
        results = probe_all()
        for result in results:
            self.repository.record_capability(result)
        return [result.model_dump(mode="json") for result in results]

    def status_snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "project": self.repository.get_project(project_id).model_dump(mode="json"),
            "next_action": self.next_action(project_id),
            "current_fact_set": self.repository.current_fact_set(project_id),
            "current_grounding_bundles": self.repository.current_grounding_bundles(
                project_id
            ),
            "deliverables": [
                item.model_dump(mode="json")
                for item in self.repository.list_deliverables(project_id)
            ],
            "segments": self.repository.list_segments(project_id),
            "artifacts": self.repository.list_artifacts(project_id),
            "tool_operations": self.repository.list_tool_operations(project_id),
            "qa_results": self.repository.list_qa_results(project_id),
            "recoverable_jobs": [
                item.model_dump(mode="json")
                for item in self.repository.recoverable_jobs(project_id)
            ],
            "budget_ledger": self.repository.budget_ledger(project_id),
            "archive": self.repository.get_archive(project_id),
        }
