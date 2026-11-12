from __future__ import annotations

from typing import Any

from ..enums import (
    ApprovalStage,
    ArtifactKind,
    GroundingBundleKind,
    ProjectStatus,
    QAStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    InvalidTransitionError,
)
from ..models import (
    ApprovalRequest,
    ArtifactCreate,
    DeliverableCreate,
    DeliverableRecord,
    DependencyRef,
    FactSetCreate,
    GroundingBundleCreate,
    Money,
    PilotContract,
    ProjectCreate,
    ProjectRecord,
    SegmentCreate,
    ShotCreate,
    SlotCreate,
)
from ..policy.guards import (
    require_active_project,
)
from ..utils import canonical_json, sha256_json
from ._shared import (
    ALLOWED_TRANSITIONS,
    APPROVAL_PREDECESSOR,
    APPROVAL_REQUIRED_STATUS,
    APPROVAL_RESULT_STATUS,
)


class ProjectsUseCases:
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
        require_active_project(
            project,
            message=f"Cannot change grounding for a {project.status.value} project",
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
