from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..enums import (
    BudgetEntryKind,
    JobStatus,
    ToolOperationStatus,
)
from ..errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    JobRecord,
    JobRequest,
    Money,
    ToolOperationRequest,
)
from ..utils import canonical_json, new_id, utc_now


class StudioReservationsStore:
    @staticmethod
    def _studio_spend_approval_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "scope_hash": row["scope_hash"],
            "max_cost_minor": row["max_cost_minor"],
            "currency": row["currency"],
            "approved_by": row["approved_by"],
            "reason": row["reason"],
            "consumed_operation_id": row["consumed_operation_id"],
            "created_at": row["created_at"],
            "consumed_at": row["consumed_at"],
        }

    def record_studio_spend_approval(
        self,
        project_id: str,
        *,
        scope_hash: str,
        max_cost: Money,
        approved_by: str,
        reason: str,
    ) -> dict[str, Any]:
        if max_cost.minor <= 0:
            raise IntegrityError(
                "Studio spend approvals must authorize a positive amount"
            )
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["currency"] != max_cost.currency:
                raise IntegrityError(
                    "Studio spend approvals must use the project currency "
                    f"{project['currency']}"
                )
            existing = connection.execute(
                """
                SELECT * FROM studio_spend_approvals
                WHERE project_id = ? AND scope_hash = ?
                  AND max_cost_minor = ? AND currency = ?
                """,
                (project_id, scope_hash, max_cost.minor, max_cost.currency),
            ).fetchone()
            if existing:
                return self._studio_spend_approval_dict(existing)
            approval_id = new_id("spendapproval")
            connection.execute(
                """
                INSERT INTO studio_spend_approvals(
                    id, project_id, scope_hash, max_cost_minor, currency,
                    approved_by, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    project_id,
                    scope_hash,
                    max_cost.minor,
                    max_cost.currency,
                    approved_by,
                    reason,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="studio_spend_approval",
                entity_id=approval_id,
                event_type="studio_spend.approved",
                payload={
                    "scope_hash": scope_hash,
                    "max_cost_minor": max_cost.minor,
                    "currency": max_cost.currency,
                    "approved_by": approved_by,
                },
            )
            row = connection.execute(
                "SELECT * FROM studio_spend_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            return self._studio_spend_approval_dict(row)

    def get_studio_spend_approval(self, approval_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM studio_spend_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Studio spend approval not found: {approval_id}")
            return self._studio_spend_approval_dict(row)

    @staticmethod
    def _specialist_handoff_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "tool_profile_id": row["tool_profile_id"],
            "operation_id": row["operation_id"],
            "budget_job_id": row["budget_job_id"],
            "spend_approval_id": row["spend_approval_id"],
            "work_packet_artifact_id": row["work_packet_artifact_id"],
            "work_packet_hash": row["work_packet_hash"],
            "input_artifact_ids": json.loads(row["input_artifact_ids_json"]),
            "output_artifact_ids": json.loads(row["output_artifact_ids_json"]),
            "result_packet_hash": row["result_packet_hash"],
            "tool_version": row["tool_version"],
            "operator_note": row["operator_note"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reserve_studio_work(
        self,
        project_id: str,
        *,
        tool_request: ToolOperationRequest,
        tool_request_hash: str,
        budget_request: JobRequest,
        budget_request_hash: str,
        work_packet_artifact_id: str,
        work_packet_hash: str,
        input_artifact_ids: tuple[str, ...],
        spend_approval_id: str | None,
        approval_scope_hash: str,
        requires_spend_approval: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], JobRecord]:
        """Atomically reserve budget, authorize Studio work and record its handoff."""

        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            existing = connection.execute(
                """
                SELECT * FROM tool_operations
                WHERE project_id = ? AND tool_profile_id = ? AND idempotency_key = ?
                """,
                (
                    project_id,
                    tool_request.tool_profile_id,
                    tool_request.idempotency_key,
                ),
            ).fetchone()
            if existing:
                if existing["request_hash"] != tool_request_hash:
                    raise ConflictError(
                        "Studio idempotency key was used for a different work packet"
                    )
                handoff = connection.execute(
                    "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                    (existing["id"],),
                ).fetchone()
                if not handoff or not handoff["budget_job_id"]:
                    raise IntegrityError(
                        "Existing Studio operation is missing its budget reservation"
                    )
                job = self._require_job(connection, handoff["budget_job_id"])
                return (
                    self._tool_operation_dict(existing),
                    self._specialist_handoff_dict(handoff),
                    self._job_from_row(job),
                )
            tool = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?",
                (tool_request.tool_profile_id,),
            ).fetchone()
            if not tool:
                raise NotFoundError(
                    f"Tool profile not found: {tool_request.tool_profile_id}"
                )
            if tool_request.operation not in set(
                json.loads(tool["supported_operations_json"])
            ):
                raise IntegrityError(
                    f"Tool profile {tool['stable_key']} does not support "
                    f"{tool_request.operation}"
                )
            if tool_request.provider_profile_id:
                raise IntegrityError(
                    "Studio budget reservations cannot also bind a provider profile"
                )
            packet = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (work_packet_artifact_id, project_id),
            ).fetchone()
            if not packet or packet["content_hash"] != work_packet_hash:
                raise IntegrityError("Studio work packet artifact/hash mismatch")
            for artifact_id in input_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Studio input artifact is not in the project: {artifact_id}"
                    )
            if budget_request.provider != "openmontage":
                raise IntegrityError(
                    "Studio budget jobs must use the openmontage provider"
                )
            if project["currency"] != budget_request.estimated_cost.currency:
                raise IntegrityError(
                    "Studio spending must reserve in the project currency "
                    f"{project['currency']}"
                )
            requested_minor = budget_request.estimated_cost.minor
            if requires_spend_approval:
                if not spend_approval_id:
                    raise ApprovalRequiredError(
                        "This Studio run requires human spend approval"
                    )
                approval = connection.execute(
                    "SELECT * FROM studio_spend_approvals WHERE id = ?",
                    (spend_approval_id,),
                ).fetchone()
                if not approval:
                    raise NotFoundError(
                        f"Studio spend approval not found: {spend_approval_id}"
                    )
                if (
                    approval["project_id"] != project_id
                    or approval["scope_hash"] != approval_scope_hash
                    or approval["max_cost_minor"] != requested_minor
                    or approval["currency"] != project["currency"]
                ):
                    raise ApprovalRequiredError(
                        "Studio spend approval does not match this exact work request"
                    )
                if approval["consumed_operation_id"]:
                    raise ConflictError(
                        "Studio spend approval has already been consumed"
                    )
            elif spend_approval_id:
                raise IntegrityError(
                    "Studio runs below the automatic limit do not use a spend approval"
                )
            exposure = project["spent_minor"] + project["reserved_minor"]
            if exposure + requested_minor > project["max_budget_minor"]:
                available = project["max_budget_minor"] - exposure
                raise BudgetExceededError(
                    f"Insufficient budget: requested {requested_minor} minor units, "
                    f"available {available}"
                )
            operation_id = new_id("op")
            job_id = new_id("job")
            handoff_id = new_id("handoff")
            connection.execute(
                """
                INSERT INTO jobs(
                    id, project_id, provider, endpoint, operation, idempotency_key,
                    request_hash, request_json, status, estimated_cost_minor,
                    reserved_cost_minor, currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    budget_request.provider,
                    budget_request.endpoint,
                    budget_request.operation,
                    budget_request.idempotency_key,
                    budget_request_hash,
                    canonical_json(budget_request.model_dump(mode="json")),
                    JobStatus.BUDGET_RESERVED.value,
                    requested_minor,
                    requested_minor,
                    budget_request.estimated_cost.currency,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET reserved_minor = reserved_minor + ?, version = version + 1,
                    updated_at = ? WHERE id = ?
                """,
                (requested_minor, now, project_id),
            )
            self._ledger(
                connection,
                project_id=project_id,
                job_id=job_id,
                kind=BudgetEntryKind.RESERVE,
                amount_minor=requested_minor,
                currency="USD",
                note="Controller-approved Studio run reservation",
            )
            connection.execute(
                """
                INSERT INTO tool_operations(
                    id, project_id, tool_profile_id, provider_profile_id, operation,
                    idempotency_key, request_hash, status, request_json,
                    output_declaration_json, limits_json, required_approval_stage,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    project_id,
                    tool_request.tool_profile_id,
                    None,
                    tool_request.operation,
                    tool_request.idempotency_key,
                    tool_request_hash,
                    ToolOperationStatus.AUTHORIZED.value,
                    canonical_json(tool_request.model_dump(mode="json")),
                    canonical_json(tool_request.output_declaration),
                    canonical_json(tool_request.limits),
                    (
                        tool_request.required_approval_stage.value
                        if tool_request.required_approval_stage
                        else None
                    ),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO specialist_handoffs(
                    id, project_id, tool_profile_id, work_packet_hash,
                    input_artifact_ids_json, output_artifact_ids_json, status,
                    created_at, updated_at, operation_id, work_packet_artifact_id,
                    budget_job_id, spend_approval_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    project_id,
                    tool_request.tool_profile_id,
                    work_packet_hash,
                    canonical_json(input_artifact_ids),
                    canonical_json(()),
                    ToolOperationStatus.AUTHORIZED.value,
                    now,
                    now,
                    operation_id,
                    work_packet_artifact_id,
                    job_id,
                    spend_approval_id,
                ),
            )
            if spend_approval_id:
                connection.execute(
                    """
                    UPDATE studio_spend_approvals
                    SET consumed_operation_id = ?, consumed_at = ? WHERE id = ?
                    """,
                    (operation_id, now, spend_approval_id),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="job",
                entity_id=job_id,
                event_type="job.budget_reserved",
                payload={
                    "request_hash": budget_request_hash,
                    "reserved_minor": requested_minor,
                    "purpose": "openmontage_run",
                },
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="tool_operation",
                entity_id=operation_id,
                event_type="tool_operation.authorized",
                payload={
                    "request_hash": tool_request_hash,
                    "tool_profile_id": tool_request.tool_profile_id,
                    "budget_job_id": job_id,
                },
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="specialist_handoff",
                entity_id=handoff_id,
                event_type="specialist_handoff.authorized",
                payload={
                    "operation_id": operation_id,
                    "work_packet_hash": work_packet_hash,
                    "budget_job_id": job_id,
                    "spend_approval_id": spend_approval_id,
                },
            )
            operation = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            handoff = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
            job = self._require_job(connection, job_id)
            return (
                self._tool_operation_dict(operation),
                self._specialist_handoff_dict(handoff),
                self._job_from_row(job),
            )

    def record_specialist_handoff(
        self,
        project_id: str,
        *,
        tool_profile_id: str,
        operation_id: str,
        work_packet_artifact_id: str,
        work_packet_hash: str,
        input_artifact_ids: tuple[str, ...],
        status: str = "authorized",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            operation = connection.execute(
                """
                SELECT id FROM tool_operations
                WHERE id = ? AND project_id = ? AND tool_profile_id = ?
                """,
                (operation_id, project_id, tool_profile_id),
            ).fetchone()
            if not operation:
                raise IntegrityError(
                    "Specialist handoff operation does not match project/tool"
                )
            packet = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (work_packet_artifact_id, project_id),
            ).fetchone()
            if not packet or packet["content_hash"] != work_packet_hash:
                raise IntegrityError("Specialist work packet artifact/hash mismatch")
            for artifact_id in input_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Specialist input artifact is not in the project: {artifact_id}"
                    )
            existing = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing:
                observed = self._specialist_handoff_dict(existing)
                expected = {
                    "work_packet_artifact_id": work_packet_artifact_id,
                    "work_packet_hash": work_packet_hash,
                    "input_artifact_ids": list(input_artifact_ids),
                }
                if any(observed[key] != value for key, value in expected.items()):
                    raise ConflictError(
                        "Specialist handoff operation already has another work packet"
                    )
                return observed
            handoff_id = new_id("handoff")
            connection.execute(
                """
                INSERT INTO specialist_handoffs(
                    id, project_id, tool_profile_id, work_packet_hash,
                    input_artifact_ids_json, output_artifact_ids_json, status,
                    created_at, updated_at, operation_id, work_packet_artifact_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    project_id,
                    tool_profile_id,
                    work_packet_hash,
                    canonical_json(input_artifact_ids),
                    canonical_json(()),
                    status,
                    now,
                    now,
                    operation_id,
                    work_packet_artifact_id,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="specialist_handoff",
                entity_id=handoff_id,
                event_type="specialist_handoff.authorized",
                payload={
                    "operation_id": operation_id,
                    "work_packet_hash": work_packet_hash,
                    "input_artifact_ids": input_artifact_ids,
                },
            )
            row = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
            return self._specialist_handoff_dict(row)

    def specialist_handoff_for_operation(self, operation_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(
                    f"Specialist handoff not found for operation: {operation_id}"
                )
            return self._specialist_handoff_dict(row)
