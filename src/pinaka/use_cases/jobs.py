from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..adapters.base import GenerationAdapter
from ..enums import (
    ArtifactKind,
    JobStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
)
from ..models import (
    DependencyRef,
    JobRecord,
    JobRequest,
    Money,
)
from ..operations import operation as catalog_operation
from ..policy.guards import require_one_take
from ..policy.secrets import reject_sensitive_payload
from ..utils import sha256_json


class JobsUseCases:
    def reserve_job(self, project_id: str, request: JobRequest) -> JobRecord:
        self._reject_sensitive_payload(request.model_dump(mode="json"))
        if request.estimated_cost.minor > 0:
            pending = self.repository.pending_variance_requests(project_id)
            if pending:
                raise ApprovalRequiredError(
                    "A cost variance is awaiting approval; decide it before new paid "
                    "work: " + ", ".join(item["id"] for item in pending)
                )
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
        """Compatibility shim over the shared credential boundary."""

        reject_sensitive_payload(value, path)

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

    def void_job(self, job_id: str, *, reason: str) -> JobRecord:
        """Release a reservation that never reached a provider.

        Only a ``BUDGET_RESERVED`` job may be voided. A submitted or unknown
        submission might already have been charged, so it must go through
        reconciliation instead of being written off here.
        """

        if not reason or not reason.strip():
            raise IntegrityError("A void reason is required for the audit log")
        self._reject_sensitive_payload({"reason": reason}, "void_job")
        job = self.repository.get_job(job_id)
        if job.status != JobStatus.BUDGET_RESERVED:
            raise ConflictError(
                "Only a BUDGET_RESERVED job can be voided; "
                f"{job.id} is {job.status.value}"
            )
        return self.settle_job(
            job_id,
            target=JobStatus.FAILED,
            actual_cost=Money(amount=Decimal(0), currency=job.reserved_cost.currency),
            error={"type": "voided", "reason": reason},
        )

    def run_adapter_once(
        self,
        project_id: str,
        request: JobRequest,
        adapter: GenerationAdapter,
        *,
        artifact_path: str,
        budget_plan_item: tuple[str, str] | None = None,
        parent_artifact_id: str | None = None,
        dependencies: tuple[DependencyRef, ...] = (),
    ) -> tuple[JobRecord, dict[str, Any] | None]:
        if request.provider == "fal":
            if request.operation not in {"image.generate", "image.edit"}:
                raise IntegrityError(
                    "fal image execution requires a declared catalog operation"
                )
            catalog_operation(request.operation).validate(
                "fal.image", model=request.endpoint
            )
            require_one_take(request.parameters)
            if budget_plan_item is None:
                raise IntegrityError("Paid fal image work requires a funded plan item")
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
                    {"type": type(error).__name__, "redacted": True},
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
            kind=ArtifactKind.DERIVATIVE
            if parent_artifact_id
            else ArtifactKind.GENERATED,
            relative_path=artifact_path,
            content=result.output_bytes,
            operation={
                "type": "generation",
                "provider": request.provider,
                "job_id": job.id,
            },
            parent_artifact_id=parent_artifact_id,
            dependencies=(
                DependencyRef(entity_type="job", entity_id=job.id),
                *dependencies,
            ),
        )
        settled = self.settle_job(
            job.id,
            target=JobStatus.SUCCEEDED,
            actual_cost=result.actual_cost,
            response=result.raw,
            asset_artifact_id=artifact["id"] if budget_plan_item else None,
        )
        return settled, artifact
