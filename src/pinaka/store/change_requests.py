from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..contracts.change_request import (
    ChangeRequestCreate,
    ChangeRequestRecord,
)
from ..errors import ConflictError, NotFoundError
from ..utils import canonical_json, new_id, utc_now


class ChangeRequestsStore:
    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ChangeRequestRecord:
        impact = json.loads(row["impact_json"]) if row["impact_json"] else None
        return ChangeRequestRecord(
            id=row["id"],
            project_id=row["project_id"],
            status=row["status"],
            document_schema=row["document_schema"],
            requested_by=row["requested_by"],
            requested_at=row["requested_at"],
            change_type=row["change_type"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            current_hash=row["current_hash"],
            requested_change=row["requested_change"],
            reason=row["reason"],
            deadline=row["deadline"],
            approver=row["approver"],
            approval_reference=row["approval_reference"],
            version_scope=row["version_scope"],
            document_hash=row["document_hash"],
            classification=row["classification"],
            impact=impact,
            revision_project_id=row["revision_project_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_change_request(
        self,
        project_id: str,
        request: ChangeRequestCreate,
        *,
        target_type: str,
        target_id: str,
        document_hash: str | None,
        impact: dict[str, Any],
        classification: str,
    ) -> ChangeRequestRecord:
        request_id = new_id("crq")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO change_requests(
                    id, project_id, status, document_schema, requested_by, requested_at,
                    change_type, target_type, target_id, current_hash,
                    requested_change, reason, deadline, approver,
                    approval_reference, version_scope, document_hash,
                    impact_json, classification, created_at, updated_at
                ) VALUES (?, ?, 'analysed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    project_id,
                    "change-request/v1",
                    request.requested_by,
                    request.requested_at,
                    request.change_type,
                    target_type,
                    target_id,
                    request.current_hash,
                    request.requested_change,
                    request.reason,
                    request.deadline,
                    request.approver,
                    request.approval_reference,
                    request.version_scope,
                    document_hash,
                    canonical_json(impact),
                    classification,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="change_request",
                entity_id=request_id,
                event_type="change_request.received",
                payload={
                    "change_type": request.change_type,
                    "target_type": target_type,
                    "target_id": target_id,
                    "classification": classification,
                },
            )
            self._append_event(
                connection,
                request_id,
                project_id,
                status="analysed",
                actor=request.requested_by,
                reason=classification,
                evidence=(f"document_hash:{document_hash or 'none'}",),
                now=now,
            )
            return self._record_from_row(self._require_request(connection, request_id))

    def get_change_request(self, request_id: str) -> ChangeRequestRecord:
        with self.database.read() as connection:
            return self._record_from_row(self._require_request(connection, request_id))

    def list_change_requests(
        self, project_id: str, status: str | None = None
    ) -> list[ChangeRequestRecord]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if status:
                rows = connection.execute(
                    "SELECT * FROM change_requests WHERE project_id = ? AND status = ? ORDER BY created_at",
                    (project_id, status),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM change_requests WHERE project_id = ? ORDER BY created_at",
                    (project_id,),
                ).fetchall()
            return [self._record_from_row(row) for row in rows]

    def record_change_request_status(
        self,
        request_id: str,
        *,
        status: str,
        actor: str,
        reason: str,
        evidence: tuple[str, ...] = (),
        revision_project_id: str | None = None,
    ) -> ChangeRequestRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            row = self._require_request(connection, request_id)
            allowed = {
                "received": {"analysed", "rejected"},
                "analysed": {"approved", "rejected"},
                "approved": {"applied"},
                "rejected": set(),
                "applied": set(),
            }
            if status not in allowed.get(row["status"], set()):
                raise ConflictError(
                    f"Change request {request_id} cannot move from "
                    f"{row['status']} to {status}"
                )
            connection.execute(
                """
                UPDATE change_requests
                SET status = ?, revision_project_id = COALESCE(?, revision_project_id),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, revision_project_id, now, request_id),
            )
            self._append_event(
                connection,
                request_id,
                row["project_id"],
                status=status,
                actor=actor,
                reason=reason,
                evidence=evidence,
                now=now,
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="change_request",
                entity_id=request_id,
                event_type=f"change_request.{status}",
                payload={"reason": reason, "evidence": list(evidence)},
            )
            return self._record_from_row(self._require_request(connection, request_id))

    def change_request_events(self, request_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM change_request_events WHERE request_id = ? ORDER BY created_at, rowid",
                (request_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "request_id": row["request_id"],
                    "project_id": row["project_id"],
                    "status": row["status"],
                    "actor": row["actor"],
                    "reason": row["reason"],
                    "evidence": json.loads(row["evidence_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def list_revision_children(self, request_id: str) -> list[str]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT id FROM projects WHERE change_request_id = ? ORDER BY created_at",
                (request_id,),
            ).fetchall()
            return [row["id"] for row in rows]

    @staticmethod
    def _require_request(
        connection: sqlite3.Connection, request_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM change_requests WHERE id = ?", (request_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Change request not found: {request_id}")
        return row

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        request_id: str,
        project_id: str,
        *,
        status: str,
        actor: str,
        reason: str,
        evidence: tuple[str, ...],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO change_request_events(
                id, request_id, project_id, status, actor, reason,
                evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("crqevt"),
                request_id,
                project_id,
                status,
                actor,
                reason,
                canonical_json(evidence),
                now,
            ),
        )
