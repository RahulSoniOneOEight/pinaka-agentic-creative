from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from ..enums import (
    JobStatus,
    ToolOperationStatus,
)
from ..errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    PinakaError,
)
from ..models import (
    DependencyRef,
    ExecutionReceiptCreate,
    Money,
    StudioResultOutputImport,
    StudioResultPacketImport,
)
from ..policy.guards import (
    require_active_project,
)
from ..studio import StudioAdapter
from ..studio_gateway import (
    StudioOpenRequest,
    StudioPending,
    StudioSubmission,
    StudioTransport,
)
from ..utils import sha256_bytes, sha256_json


class StudioExecutionUseCases:
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
        require_active_project(
            project,
            message=f"Cannot run Studio work for a {project.status.value} project",
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
