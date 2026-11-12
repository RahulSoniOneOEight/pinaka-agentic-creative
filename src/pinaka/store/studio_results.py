from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from ..enums import (
    BudgetEntryKind,
    JobStatus,
    ToolOperationStatus,
)
from ..errors import (
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    ExecutionReceiptCreate,
    Money,
)
from ..utils import canonical_json, new_id, sha256_json, utc_now


class StudioResultsStore:
    def record_specialist_result(
        self,
        operation_id: str,
        request: ExecutionReceiptCreate,
        *,
        result_packet_hash: str,
        tool_version: str | None = None,
        operator_note: str | None = None,
        approved_variance_minor: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically settle a specialist operation and its linked handoff."""

        now = utc_now()
        with self.database.transaction() as connection:
            operation = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not operation:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            handoff = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not handoff:
                raise NotFoundError(
                    f"Specialist handoff not found for operation: {operation_id}"
                )
            budget_job = (
                self._require_job(connection, handoff["budget_job_id"])
                if handoff["budget_job_id"]
                else None
            )
            cost = request.actual_cost
            if budget_job and cost is None:
                cost = Money(amount=Decimal(0), currency=budget_job["currency"])
            actual_minor = cost.minor if cost else 0
            currency = cost.currency if cost else None
            if budget_job:
                if currency != budget_job["currency"]:
                    raise IntegrityError(
                        "Studio actual cost currency does not match its reservation"
                    )
                if actual_minor > budget_job["reserved_cost_minor"]:
                    if (
                        approved_variance_minor is None
                        or approved_variance_minor != actual_minor
                    ):
                        raise BudgetExceededError(
                            "Studio actual cost exceeds the controller reservation; "
                            "manual reconciliation is required"
                        )
                    project_row = self._require_project(
                        connection, operation["project_id"]
                    )
                    if (
                        project_row["spent_minor"] + actual_minor
                        > project_row["max_budget_minor"]
                    ):
                        raise BudgetExceededError(
                            "Studio actual cost would exceed the project budget"
                        )
            elif actual_minor:
                raise IntegrityError(
                    "Paid specialist execution is missing a controller budget reservation"
                )
            elif cost is None:
                project = self._require_project(connection, operation["project_id"])
                currency = project["currency"]
            receipt_content = {
                "status": request.status.value,
                "elapsed_ms": request.elapsed_ms,
                "output_artifact_ids": request.output_artifact_ids,
                "response_hash": request.response_hash,
                "actual_cost_minor": actual_minor,
                "currency": currency,
                "details": request.details,
            }
            existing = connection.execute(
                "SELECT * FROM execution_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing:
                observed = {
                    "status": existing["status"],
                    "elapsed_ms": existing["elapsed_ms"],
                    "output_artifact_ids": json.loads(
                        existing["output_artifact_ids_json"]
                    ),
                    "response_hash": existing["response_hash"],
                    "actual_cost_minor": existing["actual_cost_minor"],
                    "currency": existing["currency"],
                    "details": json.loads(existing["details_json"]),
                }
                handoff_dict = self._specialist_handoff_dict(handoff)
                if (
                    sha256_json(observed) != sha256_json(receipt_content)
                    or handoff_dict["result_packet_hash"] != result_packet_hash
                    or handoff_dict["output_artifact_ids"]
                    != list(request.output_artifact_ids)
                ):
                    raise ConflictError(
                        "Specialist operation already has a different terminal result"
                    )
                receipt = {
                    "id": existing["id"],
                    "operation_id": operation_id,
                    **observed,
                    "created_at": existing["created_at"],
                }
                return receipt, handoff_dict
            operation_status = ToolOperationStatus(operation["status"])
            if operation_status not in {
                ToolOperationStatus.AUTHORIZED,
                ToolOperationStatus.RUNNING,
            }:
                raise ConflictError(
                    f"Operation in {operation_status.value} cannot receive a new result"
                )
            for artifact_id in request.output_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, operation["project_id"]),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Specialist output artifact is not in the project: {artifact_id}"
                    )
            if handoff["result_packet_hash"]:
                raise ConflictError(
                    "Specialist handoff has a result without a matching receipt"
                )
            if budget_job:
                job_status = JobStatus(budget_job["status"])
                terminal_jobs = {
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }
                if job_status in terminal_jobs:
                    raise ConflictError(
                        f"Studio budget job is already terminal: {job_status.value}"
                    )
                if request.status == ToolOperationStatus.OUTCOME_UNKNOWN:
                    if actual_minor:
                        raise IntegrityError(
                            "An unknown Studio outcome cannot declare a settled charge"
                        )
                    connection.execute(
                        """
                        UPDATE jobs SET status = ?, error_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            JobStatus.SUBMISSION_UNKNOWN.value,
                            canonical_json(
                                {
                                    "type": "StudioOutcomeUnknown",
                                    "operation_id": operation_id,
                                }
                            ),
                            now,
                            budget_job["id"],
                        ),
                    )
                    self._event(
                        connection,
                        project_id=operation["project_id"],
                        entity_type="job",
                        entity_id=budget_job["id"],
                        event_type="job.status_changed",
                        payload={
                            "from": job_status.value,
                            "to": JobStatus.SUBMISSION_UNKNOWN.value,
                        },
                    )
                else:
                    target_job_status = {
                        ToolOperationStatus.SUCCEEDED: JobStatus.SUCCEEDED,
                        ToolOperationStatus.FAILED: JobStatus.FAILED,
                        ToolOperationStatus.CANCELLED: JobStatus.CANCELLED,
                    }[request.status]
                    reserved = budget_job["reserved_cost_minor"]
                    connection.execute(
                        """
                        UPDATE jobs
                        SET status = ?, reserved_cost_minor = 0, actual_cost_minor = ?,
                            response_json = ?, updated_at = ? WHERE id = ?
                        """,
                        (
                            target_job_status.value,
                            actual_minor,
                            canonical_json(
                                {
                                    "operation_id": operation_id,
                                    "result_packet_hash": result_packet_hash,
                                }
                            ),
                            now,
                            budget_job["id"],
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE projects
                        SET reserved_minor = reserved_minor - ?,
                            spent_minor = spent_minor + ?, version = version + 1,
                            updated_at = ? WHERE id = ?
                        """,
                        (
                            reserved,
                            actual_minor,
                            now,
                            operation["project_id"],
                        ),
                    )
                    self._ledger(
                        connection,
                        project_id=operation["project_id"],
                        job_id=budget_job["id"],
                        kind=BudgetEntryKind.RELEASE,
                        amount_minor=reserved,
                        currency=budget_job["currency"],
                        note="Released terminal Studio run reservation",
                    )
                    if actual_minor:
                        self._ledger(
                            connection,
                            project_id=operation["project_id"],
                            job_id=budget_job["id"],
                            kind=BudgetEntryKind.CHARGE,
                            amount_minor=actual_minor,
                            currency=budget_job["currency"],
                            note="OpenMontage Studio run cost",
                        )
                    self._event(
                        connection,
                        project_id=operation["project_id"],
                        entity_type="job",
                        entity_id=budget_job["id"],
                        event_type="job.settled",
                        payload={
                            "from": job_status.value,
                            "to": target_job_status.value,
                            "reserved_released_minor": reserved,
                            "actual_cost_minor": actual_minor,
                        },
                    )
            receipt_id = new_id("receipt")
            connection.execute(
                """
                INSERT INTO execution_receipts(
                    id, operation_id, status, elapsed_ms, output_artifact_ids_json,
                    response_hash, actual_cost_minor, currency, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    operation_id,
                    request.status.value,
                    request.elapsed_ms,
                    canonical_json(request.output_artifact_ids),
                    request.response_hash,
                    actual_minor,
                    currency,
                    canonical_json(request.details),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tool_operations
                SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?
                """,
                (request.status.value, now, now, operation_id),
            )
            connection.execute(
                """
                UPDATE specialist_handoffs
                SET output_artifact_ids_json = ?, result_packet_hash = ?,
                    tool_version = ?, operator_note = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical_json(request.output_artifact_ids),
                    result_packet_hash,
                    tool_version,
                    operator_note,
                    request.status.value,
                    now,
                    handoff["id"],
                ),
            )
            self._event(
                connection,
                project_id=operation["project_id"],
                entity_type="execution_receipt",
                entity_id=receipt_id,
                event_type="execution_receipt.recorded",
                payload={
                    "operation_id": operation_id,
                    "status": request.status.value,
                    "output_count": len(request.output_artifact_ids),
                    "actual_cost_minor": actual_minor,
                },
            )
            self._event(
                connection,
                project_id=operation["project_id"],
                entity_type="specialist_handoff",
                entity_id=handoff["id"],
                event_type="specialist_handoff.completed",
                payload={
                    "operation_id": operation_id,
                    "status": request.status.value,
                    "result_packet_hash": result_packet_hash,
                    "output_artifact_ids": request.output_artifact_ids,
                },
            )
            updated_handoff = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (handoff["id"],)
            ).fetchone()
            receipt = {
                "id": receipt_id,
                "operation_id": operation_id,
                **receipt_content,
                "created_at": now,
            }
            return receipt, self._specialist_handoff_dict(updated_handoff)
