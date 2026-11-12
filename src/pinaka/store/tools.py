from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..enums import (
    ToolOperationStatus,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    ExecutionReceiptCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from ..utils import canonical_json, new_id, sha256_json, utc_now


class ToolsStore:
    @staticmethod
    def _tool_profile_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "stable_key": row["stable_key"],
            "name": row["name"],
            "integration_mode": row["integration_mode"],
            "executable_or_endpoint": row["executable_or_endpoint"],
            "supported_operations": json.loads(row["supported_operations_json"]),
            "limits": json.loads(row["limits_json"]),
            "fallback_order": json.loads(row["fallback_order_json"]),
            "capability_status": row["capability_status"],
            "version": row["version"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def register_tool_profile(self, request: ToolProfileCreate) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM tool_profiles WHERE stable_key = ?",
                (request.stable_key,),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                return self._tool_profile_dict(existing)
            if existing:
                connection.execute(
                    """
                    UPDATE tool_profiles SET
                        name = ?, integration_mode = ?, executable_or_endpoint = ?,
                        supported_operations_json = ?, limits_json = ?,
                        fallback_order_json = ?, capability_status = ?,
                        version = version + 1, content_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.name,
                        request.integration_mode.value,
                        request.executable_or_endpoint,
                        canonical_json(request.supported_operations),
                        canonical_json(request.limits),
                        canonical_json(request.fallback_order),
                        request.capability_status.value,
                        content_hash,
                        now,
                        existing["id"],
                    ),
                )
                profile_id = existing["id"]
                event_type = "tool_profile.updated"
            else:
                profile_id = new_id("tool")
                connection.execute(
                    """
                    INSERT INTO tool_profiles(
                        id, stable_key, name, integration_mode, executable_or_endpoint,
                        supported_operations_json, limits_json, fallback_order_json,
                        capability_status, content_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        request.stable_key,
                        request.name,
                        request.integration_mode.value,
                        request.executable_or_endpoint,
                        canonical_json(request.supported_operations),
                        canonical_json(request.limits),
                        canonical_json(request.fallback_order),
                        request.capability_status.value,
                        content_hash,
                        now,
                        now,
                    ),
                )
                event_type = "tool_profile.registered"
            self._event(
                connection,
                project_id=None,
                entity_type="tool_profile",
                entity_id=profile_id,
                event_type=event_type,
                payload={
                    "stable_key": request.stable_key,
                    "content_hash": content_hash,
                    "capability_status": request.capability_status.value,
                },
            )
            row = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            return self._tool_profile_dict(row)

    def list_tool_profiles(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_profiles ORDER BY stable_key"
            ).fetchall()
            return [self._tool_profile_dict(row) for row in rows]

    def get_tool_profile(self, profile_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Tool profile not found: {profile_id}")
            return self._tool_profile_dict(row)

    @staticmethod
    def _tool_operation_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "tool_profile_id": row["tool_profile_id"],
            "provider_profile_id": row["provider_profile_id"],
            "operation": row["operation"],
            "idempotency_key": row["idempotency_key"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "output_declaration": json.loads(row["output_declaration_json"]),
            "limits": json.loads(row["limits_json"]),
            "required_approval_stage": row["required_approval_stage"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reserve_tool_operation(
        self, project_id: str, request: ToolOperationRequest, request_hash: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            tool = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?", (request.tool_profile_id,)
            ).fetchone()
            if not tool:
                raise NotFoundError(
                    f"Tool profile not found: {request.tool_profile_id}"
                )
            supported = set(json.loads(tool["supported_operations_json"]))
            if request.operation not in supported:
                raise IntegrityError(
                    f"Tool profile {tool['stable_key']} does not support {request.operation}"
                )
            if request.provider_profile_id:
                provider = connection.execute(
                    "SELECT * FROM provider_profiles WHERE id = ?",
                    (request.provider_profile_id,),
                ).fetchone()
                if not provider:
                    raise NotFoundError(
                        f"Provider profile not found: {request.provider_profile_id}"
                    )
                if not bool(provider["enabled"]):
                    raise ConflictError(
                        f"Provider profile is disabled: {provider['stable_key']}"
                    )
            existing = connection.execute(
                """
                SELECT * FROM tool_operations
                WHERE project_id = ? AND tool_profile_id = ? AND idempotency_key = ?
                """,
                (project_id, request.tool_profile_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ConflictError(
                        "Tool operation idempotency key was used for another request"
                    )
                return self._tool_operation_dict(existing)
            operation_id = new_id("op")
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
                    request.tool_profile_id,
                    request.provider_profile_id,
                    request.operation,
                    request.idempotency_key,
                    request_hash,
                    ToolOperationStatus.AUTHORIZED.value,
                    canonical_json(request.model_dump(mode="json")),
                    canonical_json(request.output_declaration),
                    canonical_json(request.limits),
                    (
                        request.required_approval_stage.value
                        if request.required_approval_stage
                        else None
                    ),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="tool_operation",
                entity_id=operation_id,
                event_type="tool_operation.authorized",
                payload={
                    "request_hash": request_hash,
                    "tool_profile_id": request.tool_profile_id,
                    "provider_profile_id": request.provider_profile_id,
                },
            )
            row = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            return self._tool_operation_dict(row)

    def get_tool_operation(self, operation_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            return self._tool_operation_dict(row)

    def tool_operation_by_idempotency(
        self, project_id: str, tool_profile_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM tool_operations
                WHERE project_id = ? AND tool_profile_id = ? AND idempotency_key = ?
                """,
                (project_id, tool_profile_id, idempotency_key),
            ).fetchone()
            return self._tool_operation_dict(row) if row else None

    def mark_tool_operation_running(self, operation_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            status = ToolOperationStatus(row["status"])
            if status == ToolOperationStatus.RUNNING:
                return self._tool_operation_dict(row)
            if status != ToolOperationStatus.AUTHORIZED:
                raise ConflictError(
                    f"Tool operation cannot start from status {status.value}"
                )
            connection.execute(
                """
                UPDATE tool_operations
                SET status = ?, started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (ToolOperationStatus.RUNNING.value, now, now, operation_id),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="tool_operation",
                entity_id=operation_id,
                event_type="tool_operation.started",
                payload={"from": status.value, "to": ToolOperationStatus.RUNNING.value},
            )
            updated = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            return self._tool_operation_dict(updated)

    def record_execution_receipt(
        self, operation_id: str, request: ExecutionReceiptCreate
    ) -> dict[str, Any]:
        now = utc_now()
        if request.actual_cost and request.actual_cost.minor > 0:
            raise IntegrityError(
                "Paid execution must settle through a controller-budgeted provider job"
            )
        with self.database.transaction() as connection:
            operation = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not operation:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            specialist = connection.execute(
                "SELECT id FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if specialist:
                raise IntegrityError(
                    "Specialist operations must settle through their governed handoff"
                )
            existing = connection.execute(
                "SELECT * FROM execution_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            cost = request.actual_cost
            actual_minor = cost.minor if cost else 0
            currency = cost.currency if cost else None
            receipt_content = {
                "status": request.status.value,
                "elapsed_ms": request.elapsed_ms,
                "output_artifact_ids": request.output_artifact_ids,
                "response_hash": request.response_hash,
                "actual_cost_minor": actual_minor,
                "currency": currency,
                "details": request.details,
            }
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
                if sha256_json(observed) != sha256_json(receipt_content):
                    raise ConflictError(
                        "Execution receipt already exists with different content"
                    )
                return {"id": existing["id"], "operation_id": operation_id, **observed}
            operation_status = ToolOperationStatus(operation["status"])
            if operation_status not in {
                ToolOperationStatus.AUTHORIZED,
                ToolOperationStatus.RUNNING,
            }:
                raise ConflictError(
                    f"Operation in {operation_status.value} cannot receive a new receipt"
                )
            for artifact_id in request.output_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, operation["project_id"]),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Receipt output artifact is not in the project: {artifact_id}"
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
                SET status = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (request.status.value, now, now, operation_id),
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
            return {"id": receipt_id, "operation_id": operation_id, **receipt_content}

    def get_execution_receipt(self, operation_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM execution_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "operation_id": operation_id,
                "status": row["status"],
                "elapsed_ms": row["elapsed_ms"],
                "output_artifact_ids": json.loads(row["output_artifact_ids_json"]),
                "response_hash": row["response_hash"],
                "actual_cost_minor": row["actual_cost_minor"],
                "currency": row["currency"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }

    def list_tool_operations(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM tool_operations WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._tool_operation_dict(row) for row in rows]
