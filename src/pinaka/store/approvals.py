from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from ..enums import (
    ApprovalStatus,
    ProjectStatus,
)
from ..errors import (
    ConflictError,
    IntegrityError,
)
from ..models import (
    ApprovalRequest,
    DependencyRef,
)
from ..utils import canonical_json, new_id, utc_now


class ApprovalsStore:
    def active_approval(self, project_id: str, stage: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM approvals WHERE project_id = ? AND stage = ? AND status = ?",
                (project_id, stage, ApprovalStatus.ACTIVE.value),
            ).fetchone()
            return self._approval_dict(connection, row) if row else None

    @staticmethod
    def _approval_dict(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        dependencies = connection.execute(
            """
            SELECT entity_type, entity_id, entity_hash
            FROM approval_dependencies WHERE approval_id = ?
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "stage": row["stage"],
            "status": row["status"],
            "approved_by": row["approved_by"],
            "scope": json.loads(row["scope_json"]),
            "subject_hash": row["subject_hash"],
            "output_hash": row["output_hash"],
            "approved_at": row["approved_at"],
            "invalidated_at": row["invalidated_at"],
            "invalidation_reason": row["invalidation_reason"],
            "dependencies": [dict(item) for item in dependencies],
        }

    def create_approval(
        self,
        project_id: str,
        request: ApprovalRequest,
        dependencies: Iterable[DependencyRef],
        subject_hash: str,
        *,
        expected_project_status: ProjectStatus,
        resulting_project_status: ProjectStatus | None,
    ) -> dict[str, Any]:
        approval_id = new_id("appr")
        now = utc_now()
        dependencies = tuple(dependencies)
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["status"] != expected_project_status.value:
                raise ConflictError(
                    f"Project status changed: expected {expected_project_status.value}, "
                    f"observed {project['status']}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        id, project_id, stage, status, approved_by, scope_json,
                        subject_hash, output_hash, approved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        project_id,
                        request.stage.value,
                        ApprovalStatus.ACTIVE.value,
                        request.approved_by,
                        canonical_json(request.scope),
                        subject_hash,
                        request.output_hash,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"An active {request.stage.value} approval already exists"
                ) from error
            for dependency in dependencies:
                connection.execute(
                    """
                    INSERT INTO approval_dependencies(approval_id, entity_type, entity_id, entity_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        dependency.entity_type,
                        dependency.entity_id,
                        dependency.entity_hash,
                    ),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="approval",
                entity_id=approval_id,
                event_type="approval.created",
                payload={
                    "stage": request.stage.value,
                    "approved_by": request.approved_by,
                    "subject_hash": subject_hash,
                    "dependency_count": len(dependencies),
                },
            )
            if resulting_project_status:
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (resulting_project_status.value, now, project_id),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="project",
                    entity_id=project_id,
                    event_type="project.status_changed",
                    payload={
                        "from": project["status"],
                        "to": resulting_project_status.value,
                        "reason": f"{request.stage.value} approval {approval_id} recorded",
                    },
                )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            return self._approval_dict(connection, row)

    @staticmethod
    def _publication_authorization_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "artifact_id": row["artifact_id"],
            "artifact_hash": row["artifact_hash"],
            "channels": json.loads(row["channels_json"]),
            "authorized_by": row["authorized_by"],
            "reference": row["reference"],
            "authorized_at": row["authorized_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "created_at": row["created_at"],
        }

    def record_publication_authorization(
        self,
        project_id: str,
        *,
        artifact_id: str,
        artifact_hash: str,
        channels: tuple[str, ...],
        authorized_by: str,
        reference: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (artifact_id, project_id),
            ).fetchone()
            if not artifact or artifact["content_hash"] != artifact_hash:
                raise IntegrityError("Publication artifact/hash mismatch")
            connection.execute(
                """
                UPDATE publication_authorizations
                SET revoked_at = ?
                WHERE project_id = ? AND revoked_at IS NULL
                """,
                (now, project_id),
            )
            authorization_id = new_id("pubauth")
            connection.execute(
                """
                INSERT INTO publication_authorizations(
                    id, project_id, artifact_id, artifact_hash, channels_json,
                    authorized_by, reference, authorized_at, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_id,
                    project_id,
                    artifact_id,
                    artifact_hash,
                    canonical_json(channels),
                    authorized_by,
                    reference,
                    now,
                    expires_at,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="publication_authorization",
                entity_id=authorization_id,
                event_type="publication.authorized",
                payload={
                    "artifact_id": artifact_id,
                    "artifact_hash": artifact_hash,
                    "channels": channels,
                    "authorized_by": authorized_by,
                },
            )
            row = connection.execute(
                "SELECT * FROM publication_authorizations WHERE id = ?",
                (authorization_id,),
            ).fetchone()
            return self._publication_authorization_dict(row)

    def current_publication_authorization(
        self, project_id: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                """
                SELECT * FROM publication_authorizations
                WHERE project_id = ? AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            return self._publication_authorization_dict(row) if row else None
