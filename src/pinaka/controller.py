from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .adapters.base import GenerationAdapter
from .artifacts import ArtifactStore
from .capabilities import probe_all
from .database import Database
from .enums import (
    ApprovalStage,
    ArtifactKind,
    AssetEligibilityStatus,
    JobStatus,
    ProjectStatus,
)
from .errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    InvalidTransitionError,
    RightsEligibilityError,
)
from .models import (
    ApprovalRequest,
    ArtifactCreate,
    AssetCandidate,
    AssetEligibilityResult,
    AssetUseContext,
    DependencyRef,
    FactSetCreate,
    JobRecord,
    JobRequest,
    Money,
    ProjectCreate,
    ProjectRecord,
    ShotCreate,
    SlotCreate,
)
from .repository import Repository
from .sourcing import evaluate_asset_candidate
from .utils import sha256_json

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
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FACTS_LOCKED: {
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.SOURCING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.SOURCING: {
        ProjectStatus.FACTS_LOCKED,
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
        ProjectStatus.ARCHIVED,
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
            fact_set = self.repository.current_fact_set(project_id)
            if not fact_set:
                raise ApprovalRequiredError(
                    "A current fact set is required for this approval"
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

    def check_capabilities(self) -> list[dict[str, Any]]:
        results = probe_all()
        for result in results:
            self.repository.record_capability(result)
        return [result.model_dump(mode="json") for result in results]

    def status_snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "project": self.repository.get_project(project_id).model_dump(mode="json"),
            "current_fact_set": self.repository.current_fact_set(project_id),
            "artifacts": self.repository.list_artifacts(project_id),
            "recoverable_jobs": [
                item.model_dump(mode="json")
                for item in self.repository.recoverable_jobs(project_id)
            ],
            "budget_ledger": self.repository.budget_ledger(project_id),
        }
