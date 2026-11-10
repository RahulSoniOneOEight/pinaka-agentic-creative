from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from .adapters.animatic import DeterministicAnimaticAdapter
from .adapters.base import GenerationAdapter
from .adapters.encode import FFmpegEncodeAdapter, VideoEncodeAdapter
from .adapters.frames import ChromeFrameCapture, FrameCaptureAdapter
from .adapters.mux import FFmpegMuxAdapter, MuxAdapter
from .adapters.narration import KokoroNarrationAdapter, NarrationAdapter
from .adapters.tool import ToolAdapter
from .animatic import build_timeline, decimal_seconds
from .artifacts import ArtifactStore
from .capabilities import probe_all
from .database import Database
from .enums import (
    ApprovalStage,
    ArtifactKind,
    AssetEligibilityStatus,
    CapabilityStatus,
    GroundingBundleKind,
    JobStatus,
    ProjectStatus,
    ToolIntegrationMode,
    ToolOperationStatus,
)
from .errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    InvalidTransitionError,
    NotFoundError,
    RightsEligibilityError,
)
from .models import (
    ApprovalRequest,
    ArtifactCreate,
    AssetCandidate,
    AssetEligibilityResult,
    AssetUseContext,
    DeliverableCreate,
    DeliverableRecord,
    DependencyRef,
    ExecutionReceiptCreate,
    FactSetCreate,
    GroundingBundleCreate,
    JobRecord,
    JobRequest,
    Money,
    PilotContract,
    ProjectCreate,
    ProjectRecord,
    ProviderModelProfileCreate,
    SegmentCreate,
    ShotCreate,
    SlotCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from .qa import build_report, run_animatic_qa, run_voiced_animatic_qa
from .repository import Repository
from .sourcing import evaluate_asset_candidate
from .utils import canonical_json, sha256_bytes, sha256_file, sha256_json

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

    def run_tool_once(
        self,
        project_id: str,
        request: ToolOperationRequest,
        adapter: ToolAdapter,
        *,
        artifact_path: str,
        artifact_kind: ArtifactKind = ArtifactKind.DERIVATIVE,
        dependencies: tuple[DependencyRef, ...] = (),
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
        )

    def run_adapter_once(
        self,
        project_id: str,
        request: JobRequest,
        adapter: GenerationAdapter,
        *,
        artifact_path: str,
    ) -> tuple[JobRecord, dict[str, Any] | None]:
        if request.provider != adapter.provider_name:
            raise IntegrityError(
                f"Request provider {request.provider!r} does not match adapter "
                f"{adapter.provider_name!r}"
            )
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
                "Obtain human approval for the normalized brief and zero-spend provider policy.",
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
